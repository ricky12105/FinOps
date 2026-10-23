"""Validate, stage, and materialize versioned FinOps financial foundation bundles inside Fabric Spark.

Enforces SHA-256 verification of the incoming ZIP bundle, the pinned `finops_foundation.py`
runner, and every member artifact before rebuilding or replaying the candidate in OneLake and
materializing version-pinned `silver.*`, `gold.*`, and `control.*` Delta tables.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sys
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from zipfile import ZipFile

import pyarrow
import pyarrow.parquet as parquet


def file_hash(path: Path | str) -> str:
    """Compute the lowercase SHA-256 hex digest of a file."""
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def copy_verified(source: Path, destination: Path) -> None:
    """Copy `source` to `destination` without overwriting and verify post-copy SHA-256."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        with source.open("rb") as incoming, destination.open("xb") as outgoing:
            shutil.copyfileobj(incoming, outgoing)
    if file_hash(source) != file_hash(destination):
        raise ValueError("Cloud copy hash mismatch; preserve for quarantine review")


def validate_and_stage(
    bundle: Path | str,
    expected_bundle_hash: str,
    expected_core_hash: str,
    target: Path | str,
) -> dict[str, Any]:
    """Validate bundle/runner SHA-256 digests, rebuild in scratch space, and stage to OneLake."""
    bundle, target = Path(bundle), Path(target).resolve()
    if file_hash(bundle) != expected_bundle_hash:
        raise ValueError("Bundle hash mismatch")
    with TemporaryDirectory() as temporary:
        scratch = Path(temporary)
        incoming = scratch / "incoming"
        incoming.mkdir()
        with ZipFile(bundle) as archive:
            descriptor = json.loads(archive.read("bundle.json"))
            if descriptor["coreSha256"] != expected_core_hash:
                raise ValueError("Unapproved runner hash")
            names = archive.namelist()
            if len(names) != len(set(names)) or set(names) != set(descriptor["files"]) | {"bundle.json"}:
                raise ValueError("Unexpected bundle members")
            for name, expected in descriptor["files"].items():
                if Path(name).name != name or "/" in name or "\\" in name:
                    raise ValueError("Unsafe bundle member")
                payload = archive.read(name)
                if hashlib.sha256(payload).hexdigest() != expected:
                    raise ValueError("Bundle member hash mismatch")
                (incoming / name).write_bytes(payload)
        runner_path = incoming / "finops_foundation.py"
        if file_hash(runner_path) != expected_core_hash:
            raise ValueError("Runner bytes do not match approval")
        specification = importlib.util.spec_from_file_location("pinned_financial_runner", runner_path)
        runner = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(runner)
        version = descriptor["expectedVersionId"]
        existing = target / "versions" / version / "manifest.json"
        local_root = scratch / "candidate"
        replay = existing.exists()
        validation_root = target if replay else local_root
        try:
            if replay:
                runner.verified_version(target, version)
            else:
                result = runner.build(incoming / descriptor["selectionFile"], local_root)
                if result["versionId"] != version:
                    raise ValueError("Cloud candidate identity mismatch")
            folder = validation_root / "versions" / version
            with localcontext() as context:
                context.prec = 100
                expected_total = Decimal(descriptor["expectedExactBilledCost"])
                for name in ("silver", "gold_daily", "gold_monthly"):
                    table = parquet.read_table(folder / f"{name}.parquet")
                    if sum(table.column("BilledCost").to_pylist(), Decimal(0)) != expected_total:
                        raise ValueError(f"Exact cloud readback failed: {name}")
                    if name == "silver" and table.num_rows != descriptor["expectedRows"]:
                        raise ValueError("Cloud Silver row count mismatch")
                    if name == "gold_monthly":
                        monthly = table.to_pylist()
                        if len(monthly) != descriptor["expectedMonths"] or any(abs(row["ReconciliationDelta"]) > runner.TOLERANCE for row in monthly):
                            raise ValueError("Cloud monthly reconciliation failed")
            if not replay:
                paths = [path for path in local_root.rglob("*") if path.is_file()]
                for path in sorted(paths, key=lambda entry: (entry.name == "manifest.json", str(entry))):
                    copy_verified(path, target / path.relative_to(local_root))
                runner.verified_version(target, version)
        except Exception as error:
            target.mkdir(parents=True, exist_ok=True)
            runner.event(target, "Quarantined", versionId=version, message=str(error), errorType=type(error).__name__)
            for path in local_root.glob("bronze/**/*"):
                if path.is_file():
                    copy_verified(path, target / path.relative_to(local_root))
            raise
        return {
            "versionId": version,
            "status": "Validated replay" if replay else "Validated candidate",
            "rows": descriptor["expectedRows"],
            "exactBilledCost": descriptor["expectedExactBilledCost"],
            "bundleSha256": expected_bundle_hash,
            "coreSha256": expected_core_hash,
            "pythonVersion": sys.version.split()[0],
            "pyarrowVersion": pyarrow.__version__,
            "validatedAtUtc": datetime.now(timezone.utc).isoformat(),
            "published": (target / "published.json").exists(),
        }


def materialize_candidates(spark, target, relative_root, validation):
    version = validation["versionId"]
    folder = Path(target) / "versions" / version
    for schema in ("silver", "gold", "control"):
        spark.sql(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    tables = {}
    for artifact, schema, label in (("silver", "silver", "charges"), ("gold_daily", "gold", "daily"), ("gold_monthly", "gold", "monthly")):
        table_name = f"{schema}.{label}_{version[:16]}"
        source = spark.read.parquet(f"Files/{relative_root}/versions/{version}/{artifact}.parquet")
        if not spark.catalog.tableExists(table_name):
            source.coalesce(1).write.format("delta").mode("errorifexists").saveAsTable(table_name)
        expected = parquet.read_table(folder / f"{artifact}.parquet")
        names = expected.schema.names
        expected_rows = Counter(tuple(row[name] for name in names) for row in expected.to_pylist())
        observed_rows = Counter(tuple(row[name] for name in names) for row in spark.table(table_name).collect())
        if observed_rows != expected_rows:
            raise ValueError(f"Delta table readback mismatch: {table_name}")
        tables[artifact] = table_name
    control_table = f"control.validation_{version[:16]}"
    record = {name: str(value) for name, value in {**validation, "tables": json.dumps(tables, sort_keys=True)}.items()}
    if not spark.catalog.tableExists(control_table):
        spark.createDataFrame([record]).write.format("delta").mode("errorifexists").saveAsTable(control_table)
    return {**validation, "tables": tables, "controlTable": control_table}