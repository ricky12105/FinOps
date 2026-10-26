"""Databricks operational medallion adapter (`system.billing.usage`, `query.history`, `warehouses`).

Extracts bounded snapshots via the Databricks CLI, normalizes signed DBU usage (`ORIGINAL`,
`RETRACTION`, `RESTATEMENT`) and SQL warehouse statement outcomes without exposing raw SQL text
or user PII, verifies exact DBU and statement count conservation, and materializes versioned
Bronze/Silver/Gold Delta tables in OneLake.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, localcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

import pyarrow as arrow
import pyarrow.parquet as parquet

from finops_foundation import digest, encode, immutable_json, number, read_json


VERSION: str = "databricks-operational-1"
SOURCES: dict[str, tuple[str, str, str, list[str]]] = {
    "usage": ("system.billing.usage", "usage_date", "record_id", [
        "account_id", "workspace_id", "record_id", "sku_name", "cloud", "usage_start_time", "usage_end_time", "usage_date",
        "CAST(usage_quantity AS STRING) AS usage_quantity", "usage_unit", "record_type", "ingestion_date", "billing_origin_product", "custom_tags",
        "usage_metadata.warehouse_id AS warehouse_id", "usage_metadata.notebook_id AS notebook_id", "usage_metadata.cluster_id AS cluster_id",
        "usage_metadata.job_id AS job_id", "usage_metadata.job_run_id AS job_run_id", "product_features.is_serverless AS is_serverless",
    ]),
    "queries": ("system.query.history", "start_time", "statement_id", [
        "workspace_id", "statement_id", "start_time", "end_time", "execution_status", "statement_type",
        "compute.warehouse_id AS warehouse_id", "total_duration_ms", "execution_duration_ms", "read_rows", "read_bytes", "written_rows", "written_bytes",
    ]),
    "warehouses": ("system.compute.warehouses", "change_time", "warehouse_id, change_time", [
        "workspace_id", "warehouse_id", "warehouse_name", "warehouse_type", "warehouse_size", "change_time", "delete_time",
    ]),
}


def query_sql(config: dict[str, Any], name: str) -> tuple[str, str]:
    """Build a bounded, PII-free SQL query for a supported Databricks system table."""
    start, end = date.fromisoformat(config["startDate"]), date.fromisoformat(config["endDateExclusive"])
    workspace = str(config["sourceWorkspaceId"])
    if start >= end or not re.fullmatch(r"\d+", workspace):
        raise ValueError("Invalid source scope or time window")
    table, time_field, order, fields = SOURCES[name]
    lower = "" if name == "warehouses" else f" AND {time_field} >= '{start}'"
    where = f"workspace_id = '{workspace}'{lower} AND {time_field} < '{end}'"
    # The table and projection come from SOURCES; dates and workspace ID are parsed and validated above.
    return f"SELECT {', '.join(fields)} FROM {table} WHERE {where}", order  # noqa: S608  # nosec B608


def cli_query(config: dict[str, Any], sql: str) -> tuple[list[dict[str, Any]], bytes]:
    """Execute a read-only SQL query via the Databricks CLI and parse JSON output with Decimal precision."""
    profile = str(config.get("profile", "DEFAULT"))
    warehouse_id = str(config.get("warehouseId", ""))
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", profile) or not re.fullmatch(r"[A-Za-z0-9_-]+", warehouse_id):
        raise ValueError("Invalid Databricks CLI profile or warehouse identifier")
    dbx_bin = shutil.which("databricks") or "databricks"
    command = [
        dbx_bin,
        "experimental",
        "aitools",
        "tools",
        "query",
        sql,
        "--profile",
        profile,
        "--warehouse",
        warehouse_id,
        "--output",
        "json",
    ]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", check=False, shell=False, timeout=120)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "Databricks query failed")
    rows = json.loads(result.stdout, parse_float=Decimal)
    if not isinstance(rows, list):
        raise ValueError("Unexpected CLI result shape")
    return rows, result.stdout.encode("utf-8")


def extract(config_path, destination):
    config = read_json(config_path)
    destination = Path(destination).resolve()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", config["customerId"]):
        raise ValueError("Invalid customer ID")
    folder = destination / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") / uuid.uuid4().hex
    folder.mkdir(parents=True, exist_ok=False)
    datasets = {}
    for name in SOURCES:
        sql, order = query_sql(config, name)
        count_sql = f"SELECT COUNT(*) AS row_count FROM ({sql}) scoped"  # noqa: S608  # nosec B608
        count_rows, count_bytes = cli_query(config, count_sql)
        expected = int(count_rows[0]["row_count"])
        if expected > config["maximumRowsPerDataset"]:
            raise ValueError("Extraction exceeds approved row bound")
        immutable_json(folder / f"{name}-count.json", count_rows)
        parts = []
        for offset in range(0, expected, config["pageSize"]):
            page_sql = f"SELECT to_json(struct(*)) AS payload FROM ({sql} ORDER BY {order} LIMIT {config['pageSize']} OFFSET {offset}) page"  # noqa: S608  # nosec B608
            rows, payload = cli_query(config, page_sql)
            if len(rows) != min(config["pageSize"], expected - offset):
                raise ValueError("Incomplete or truncated Databricks export page")
            filename = f"{name}-{offset:08d}.json"
            (folder / filename).write_bytes(payload)
            parts.append({"path": filename, "rows": len(rows), "sha256": digest(folder / filename)})
        datasets[name] = {"source": SOURCES[name][0], "sql": sql, "rows": expected, "parts": parts}
    manifest = {"adapterVersion": VERSION, "customerId": config["customerId"], "sourceWorkspaceId": config["sourceWorkspaceId"],
                "profile": config["profile"], "warehouseId": config["warehouseId"], "startDate": config["startDate"],
                "endDateExclusive": config["endDateExclusive"], "observedAtUtc": datetime.now(timezone.utc).isoformat(), "datasets": datasets,
                "snapshotSemantics": "Complete-window snapshot; CLI pages are not a source transaction"}
    immutable_json(folder / "manifest.json", manifest)
    return folder


def load_dataset(folder, manifest, name):
    rows = []
    dataset = manifest["datasets"][name]
    for part in dataset["parts"]:
        path = folder / part["path"]
        if path.parent != folder or digest(path) != part["sha256"]:
            raise ValueError("Source part path or hash mismatch")
        page = read_json(path)
        if len(page) != part["rows"]:
            raise ValueError("Source page row count mismatch")
        rows.extend(json.loads(record["payload"], parse_float=Decimal) for record in page)
    if len(rows) != dataset["rows"]:
        raise ValueError("Source dataset count mismatch")
    return rows


def normalize(records, manifest, name):
    result, seen = [], set()
    for source in records:
        if str(source["workspace_id"]) != manifest["sourceWorkspaceId"]:
            raise ValueError("Workspace scope mismatch")
        row = dict(source)
        key = row["record_id"] if name == "usage" else row["statement_id"] if name == "queries" else (row["warehouse_id"], row["change_time"])
        if not key or key in seen:
            raise ValueError("Missing or duplicate source key")
        seen.add(key)
        row.update(customer_id=manifest["customerId"], observed_at_utc=manifest["observedAtUtc"])
        if name == "usage":
            row["usage_quantity"] = number(row["usage_quantity"])
            if not (manifest["startDate"] <= row["usage_date"] < manifest["endDateExclusive"]):
                raise ValueError("Usage outside selected window")
            if row["record_type"] not in {"ORIGINAL", "RETRACTION", "RESTATEMENT"} or row["usage_unit"] != "DBU":
                raise ValueError("Unsupported usage unit or correction type")
            row["custom_tags"] = json.dumps(row.get("custom_tags") or {}, sort_keys=True)
            row["is_serverless"] = str(row.get("is_serverless")).lower()
            row["attribution_type"] = "Warehouse" if row.get("warehouse_id") else "Notebook" if row.get("notebook_id") else "Platform/Unallocated"
            row["attribution_id"] = row.get("warehouse_id") or row.get("notebook_id") or "Unallocated"
        elif name == "queries":
            if not (manifest["startDate"] <= row["start_time"][:10] < manifest["endDateExclusive"]):
                raise ValueError("Query outside selected window")
            for field in ("total_duration_ms", "execution_duration_ms", "read_rows", "read_bytes", "written_rows", "written_bytes"):
                row[field] = int(row[field]) if row.get(field) is not None else None
                if row[field] is not None and row[field] < 0:
                    raise ValueError("Negative query metric")
        result.append(row)
    return result


def aggregate_usage(rows):
    buckets = {}
    with localcontext() as context:
        context.prec = 100
        for row in rows:
            key = (row["customer_id"], row["workspace_id"], row["usage_date"], row["sku_name"], row["usage_unit"], row["billing_origin_product"], row["attribution_type"], row["attribution_id"])
            if key not in buckets:
                buckets[key] = dict(zip(("customer_id", "workspace_id", "usage_date", "sku_name", "usage_unit", "billing_origin_product", "attribution_type", "attribution_id"), key, strict=True), usage_quantity=Decimal(0), usage_records=0, financial_treatment="Operational DBUs; not billed cost")
            buckets[key]["usage_quantity"] += row["usage_quantity"]
            buckets[key]["usage_records"] += 1
    return list(buckets.values())


def aggregate_queries(rows):
    buckets = {}
    for row in rows:
        key = (row["customer_id"], row["workspace_id"], row["start_time"][:10], row.get("warehouse_id"), row["execution_status"])
        if key not in buckets:
            buckets[key] = dict(zip(("customer_id", "workspace_id", "query_date", "warehouse_id", "execution_status"), key, strict=True), statement_count=0, execution_duration_ms=0, total_duration_ms=0, read_bytes=0)
        bucket = buckets[key]
        bucket["statement_count"] += 1
        for field in ("execution_duration_ms", "total_duration_ms", "read_bytes"):
            bucket[field] = bucket[field] + row[field] if bucket[field] is not None and row[field] is not None else None
    return list(buckets.values())


USAGE_COLUMNS = ["account_id", "workspace_id", "record_id", "sku_name", "cloud", "usage_start_time", "usage_end_time", "usage_date", "usage_quantity", "usage_unit", "record_type", "ingestion_date", "billing_origin_product", "custom_tags", "warehouse_id", "notebook_id", "cluster_id", "job_id", "job_run_id", "is_serverless", "customer_id", "observed_at_utc", "attribution_type", "attribution_id"]
QUERY_COLUMNS = ["workspace_id", "statement_id", "start_time", "end_time", "execution_status", "statement_type", "warehouse_id", "total_duration_ms", "execution_duration_ms", "read_rows", "read_bytes", "written_rows", "written_bytes", "customer_id", "observed_at_utc"]
WAREHOUSE_COLUMNS = ["workspace_id", "warehouse_id", "warehouse_name", "warehouse_type", "warehouse_size", "change_time", "delete_time", "customer_id", "observed_at_utc"]
INTEGER_COLUMNS = {"total_duration_ms", "execution_duration_ms", "read_rows", "read_bytes", "written_rows", "written_bytes", "record_ordinal", "usage_records", "statement_count"}


def write_operational_table(path, rows, columns):
    schema = arrow.schema([(name, arrow.decimal128(38, 18) if name == "usage_quantity" else arrow.int64() if name in INTEGER_COLUMNS else arrow.string()) for name in columns])
    parquet.write_table(arrow.Table.from_pylist(rows, schema=schema), path)


def prepare(snapshot):
    snapshot = Path(snapshot).resolve()
    manifest = read_json(snapshot / "manifest.json")
    adapter_hash = digest(Path(__file__))
    batch_id = hashlib.sha256(encode({"manifestSha256": digest(snapshot / "manifest.json"), "adapterSha256": adapter_hash})).hexdigest()
    datasets = {name: normalize(load_dataset(snapshot, manifest, name), manifest, name) for name in SOURCES}
    daily_usage = aggregate_usage(datasets["usage"])
    daily_queries = aggregate_queries(datasets["queries"])
    with localcontext() as context:
        context.prec = 100
        total = sum((row["usage_quantity"] for row in datasets["usage"]), Decimal(0))
        if total != sum((row["usage_quantity"] for row in daily_usage), Decimal(0)):
            raise ValueError("Operational DBU conservation failed")
    if sum(row["statement_count"] for row in daily_queries) != len(datasets["queries"]):
        raise ValueError("Statement conservation failed")
    output = snapshot / f"prepared-{batch_id[:16]}"
    if (output / "receipt.json").exists():
        receipt = read_json(output / "receipt.json")
        for name, expected in receipt["artifacts"].items():
            if digest(output / name) != expected:
                raise ValueError("Prepared artifact hash mismatch")
        bundle = snapshot / f"databricks-{batch_id}.zip"
        if not bundle.exists():
            raise ValueError("Prepared batch bundle is missing")
        return {**receipt, "bundleFile": str(bundle), "bundleSha256": digest(bundle)}
    output.mkdir(parents=True, exist_ok=True)
    silver_columns = {"usage": USAGE_COLUMNS, "queries": QUERY_COLUMNS, "warehouses": WAREHOUSE_COLUMNS}
    for name in SOURCES:
        bronze = []
        for part in manifest["datasets"][name]["parts"]:
            for ordinal, record in enumerate(read_json(snapshot / part["path"]), start=1):
                bronze.append({"customer_id": manifest["customerId"], "batch_id": batch_id, "source_name": manifest["datasets"][name]["source"],
                               "source_file": part["path"], "source_sha256": part["sha256"], "record_ordinal": ordinal,
                               "ingested_at_utc": manifest["observedAtUtc"], "source_json": record["payload"]})
        write_operational_table(output / f"bronze_{name}.parquet", bronze, ["customer_id", "batch_id", "source_name", "source_file", "source_sha256", "record_ordinal", "ingested_at_utc", "source_json"])
        write_operational_table(output / f"silver_{name}.parquet", datasets[name], silver_columns[name])
    write_operational_table(output / "gold_usage_daily.parquet", daily_usage, ["customer_id", "workspace_id", "usage_date", "sku_name", "usage_unit", "billing_origin_product", "attribution_type", "attribution_id", "usage_quantity", "usage_records", "financial_treatment"])
    write_operational_table(output / "gold_queries_daily.parquet", daily_queries, ["customer_id", "workspace_id", "query_date", "warehouse_id", "execution_status", "statement_count", "execution_duration_ms", "total_duration_ms", "read_bytes"])
    receipt = {"batchId": batch_id, "adapterVersion": VERSION, "adapterSha256": adapter_hash, "customerId": manifest["customerId"],
               "workspaceId": manifest["sourceWorkspaceId"], "observedAtUtc": manifest["observedAtUtc"], "startDate": manifest["startDate"],
               "endDateExclusive": manifest["endDateExclusive"], "counts": {name: len(rows) for name, rows in datasets.items()},
               "exactNetDBUs": str(total), "financialTreatment": "Operational DBUs only; not additive billed cost", "publishedToLiveModel": False,
               "artifacts": {path.name: digest(path) for path in output.glob("*.parquet")}}
    immutable_json(output / "receipt.json", receipt)
    bundle = snapshot / f"databricks-{batch_id}.zip"
    if not bundle.exists():
        with ZipFile(bundle, "x", compression=ZIP_DEFLATED) as archive:
            for path in sorted(snapshot.glob("*.json")):
                archive.write(path, f"source/{path.name}")
            for path in sorted(output.iterdir()):
                archive.write(path, f"prepared/{path.name}")
    return {**receipt, "bundleFile": str(bundle), "bundleSha256": digest(bundle)}


def materialize(spark, root, receipt):
    from collections import Counter

    root = Path(root)
    batch_id = receipt["batchId"]
    tables = {}
    for schema in ("bronze", "silver", "gold", "control"):
        spark.sql(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    for filename, expected_hash in receipt["artifacts"].items():
        source_path = root / "prepared" / filename
        if digest(source_path) != expected_hash:
            raise ValueError("Uploaded Parquet hash mismatch")
        schema, label = filename.removesuffix(".parquet").split("_", 1)
        table_name = f"{schema}.dbx_{label}_{batch_id[:16]}"
        source = parquet.read_table(source_path)
        from pyspark.sql.types import DecimalType, LongType, StringType, StructField, StructType

        fields = [StructField(field.name, DecimalType(38, 18) if arrow.types.is_decimal(field.type) else LongType() if arrow.types.is_integer(field.type) else StringType(), True) for field in source.schema]
        if not spark.catalog.tableExists(table_name):
            spark.createDataFrame(source.to_pylist(), StructType(fields)).coalesce(1).write.format("delta").mode("errorifexists").saveAsTable(table_name)
        names = source.schema.names
        if Counter(tuple(row[name] for name in names) for row in source.to_pylist()) != Counter(tuple(row[name] for name in names) for row in spark.table(table_name).collect()):
            raise ValueError(f"Delta readback mismatch: {table_name}")
        tables[filename.removesuffix(".parquet")] = table_name
    record = {"batch_id": batch_id, "status": "Validated operational snapshot", "exact_net_dbus": receipt["exactNetDBUs"],
              "observed_at_utc": receipt["observedAtUtc"], "financial_treatment": receipt["financialTreatment"],
              "source_counts_json": json.dumps(receipt["counts"], sort_keys=True), "tables_json": json.dumps(tables, sort_keys=True)}
    control_table = f"control.dbx_ingestion_{batch_id[:16]}"
    if not spark.catalog.tableExists(control_table):
        spark.createDataFrame([record]).write.format("delta").mode("errorifexists").saveAsTable(control_table)
    return {**receipt, "tables": tables, "controlTable": control_table}


def run_in_fabric(spark, bundle, expected_bundle_hash, destination):
    from collections import Counter
    from run_foundation_fabric import copy_verified

    bundle, destination = Path(bundle), Path(destination)
    if digest(bundle) != expected_bundle_hash:
        raise ValueError("Unapproved Databricks bundle")
    with TemporaryDirectory() as temporary:
        scratch = Path(temporary)
        with ZipFile(bundle) as archive:
            for name in archive.namelist():
                parts = name.split("/")
                if len(parts) != 2 or parts[0] not in {"source", "prepared"} or not parts[1] or Path(parts[1]).name != parts[1] or "\\" in name:
                    raise ValueError("Unsafe bundle path")
                path = scratch / name
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("xb") as output:
                    output.write(archive.read(name))
        reference = read_json(scratch / "prepared" / "receipt.json")
        if reference["adapterSha256"] != digest(Path(__file__)):
            raise ValueError("Source bundle and deployed adapter disagree")
        rebuilt = prepare(scratch / "source")
        if rebuilt["batchId"] != reference["batchId"] or rebuilt["counts"] != reference["counts"] or rebuilt["exactNetDBUs"] != reference["exactNetDBUs"]:
            raise ValueError("Cloud source rebuild disagrees with reference")
        rebuilt_directory = scratch / "source" / f"prepared-{rebuilt['batchId'][:16]}"
        target = destination / rebuilt["batchId"]
        for filename, reference_hash in reference["artifacts"].items():
            expected = scratch / "prepared" / filename
            if digest(expected) != reference_hash:
                raise ValueError("Reference artifact hash mismatch")
            reference_table, actual = parquet.read_table(expected), parquet.read_table(rebuilt_directory / filename)
            names = reference_table.schema.names
            if Counter(tuple(row[name] for name in names) for row in reference_table.to_pylist()) != Counter(tuple(row[name] for name in names) for row in actual.to_pylist()):
                raise ValueError(f"Cloud transformation mismatch: {filename}")
        for path in (scratch / "source").glob("*.json"):
            copy_verified(path, target / "source" / path.name)
        for path in rebuilt_directory.iterdir():
            copy_verified(path, target / "prepared" / path.name)
        result = materialize(spark, target, rebuilt)
        result.pop("bundleFile", None)
        result.update(bundleSha256=expected_bundle_hash, pythonRuntime="Fabric builtin", validatedAtUtc=datetime.now(timezone.utc).isoformat())
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bounded manual Databricks operational ingestion")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--snapshot", type=Path)
    args = parser.parse_args()
    if args.snapshot:
        print(json.dumps(prepare(args.snapshot), indent=2))
    elif args.config and args.output:
        print(json.dumps({"snapshot": str(extract(args.config, args.output))}))
    else:
        parser.error("Supply --snapshot to prepare, or --config and --output to extract")
