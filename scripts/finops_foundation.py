"""Authoritative financial foundation adapter for Azure ActualCost and FOCUS 1.0r2 exports.

Preserves exact source decimal precision (Decimal(38, scale) in Parquet with precision-100
arithmetic), enforces strict schema and SHA-256 content verification on every source part,
archives raw evidence to Bronze, and reconciles monthly totals against independent native
Azure Cost Management queries within USD 0.0001 tolerance before staging Silver and Gold tables.
"""

from __future__ import annotations

import argparse
import calendar
import csv
import hashlib
import json
import os
import re
import shutil
import uuid
from collections import defaultdict
from collections.abc import Iterator
from datetime import date, datetime, timezone
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any

import pyarrow as arrow
import pyarrow.parquet as parquet


ADAPTER_VERSION: str = "foundation-1"
ADAPTERS: set[str] = {"actualcost-2021-10-01", "focus-1.0r2"}
TOLERANCE: Decimal = Decimal("0.0001")


def encode(value: Any) -> bytes:
    """Serialize `value` to canonical ASCII-safe, key-sorted UTF-8 JSON bytes."""
    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True).encode("utf-8")


def digest(path: Path | str) -> str:
    """Return the lowercase SHA-256 hex digest of the file at `path`."""
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def read_json(path: Path | str) -> Any:
    """Parse a UTF-8/UTF-8-SIG JSON file, decoding all floating-point numbers as exact `Decimal`."""
    return json.loads(Path(path).read_text(encoding="utf-8-sig"), parse_float=Decimal)


def immutable_json(path: Path, value: Any) -> None:
    """Write `value` as canonical JSON to `path`, raising `ValueError` if conflicting bytes exist."""
    payload = encode(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != payload:
            raise ValueError(f"Immutable content conflict: {path.name}")
        return
    with path.open("xb") as output:
        output.write(payload)


def number(value: str | Decimal | int) -> Decimal:
    """Convert `value` to a finite `Decimal`, rejecting binary `float` or `bool` inputs."""
    if not isinstance(value, (str, Decimal, int)) or isinstance(value, bool):
        raise ValueError("Financial values must not pass through binary float")
    result = Decimal(value)
    if not result.is_finite():
        raise ValueError("Non-finite financial value")
    return result


def day(value: datetime | date | str) -> str:
    """Normalize a date or timestamp value into an ISO-8601 `YYYY-MM-DD` string."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()[:10]
    for pattern in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%Y %H:%M:%S"):
        try:
            return datetime.strptime(str(value)[:19], pattern).date().isoformat()
        except ValueError:
            pass
    return date.fromisoformat(str(value)[:10]).isoformat()


def schema_digest(path: Path, adapter: str) -> str:
    """Verify the physical schema of a legacy ActualCost CSV (65 cols) or FOCUS Parquet (96 cols) and return its SHA-256."""
    if adapter == "actualcost-2021-10-01":
        with path.open(encoding="utf-8-sig", newline="") as source:
            fields = next(csv.reader(source))
        if len(fields) != 65 or len(set(fields)) != len(fields):
            raise ValueError("Expected the 65-column legacy ActualCost schema")
        descriptor: Any = fields
    elif adapter == "focus-1.0r2":
        schema = parquet.read_schema(path)
        if len(schema) != 96:
            raise ValueError("Expected the 96-column FOCUS 1.0r2 schema")
        for field in ("BilledCost", "EffectiveCost"):
            if not arrow.types.is_decimal(schema.field(field).type):
                raise ValueError(f"FOCUS {field} must have a decimal physical type")
        descriptor = [(field.name, str(field.type)) for field in schema]
    else:
        raise ValueError("Unsupported source adapter")
    return hashlib.sha256(encode(descriptor)).hexdigest()


def source_rows(path: Path, adapter: str) -> Iterator[dict[str, Any]]:
    """Yield canonical charge records from a verified ActualCost CSV or FOCUS 1.0r2 Parquet part."""
    if adapter == "actualcost-2021-10-01":
        with path.open(encoding="utf-8-sig", newline="") as source:
            for row in csv.DictReader(source):
                if None in row or any(value is None for value in row.values()):
                    raise ValueError("Malformed CSV record")
                yield {
                    "SubscriptionId": row["SubscriptionId"],
                    "Currency": row["billingCurrency"],
                    "PeriodStart": day(row["billingPeriodStartDate"]) if row["billingPeriodStartDate"] else None,
                    "ChargeDate": day(row["date"]),
                    "BilledCost": number(row["costInBillingCurrency"]),
                    "EffectiveCost": None,
                    "ResourceId": row["ResourceId"],
                    "Service": row["consumedService"],
                    "ChargeCategory": row["chargeType"],
                }
    else:
        for batch in parquet.ParquetFile(path).iter_batches():
            for row in batch.to_pylist():
                yield {
                    "SubscriptionId": row["SubAccountId"],
                    "Currency": row["BillingCurrency"],
                    "PeriodStart": day(row["BillingPeriodStart"]),
                    "ChargeDate": day(row["ChargePeriodStart"]),
                    "BilledCost": number(row["BilledCost"]),
                    "EffectiveCost": number(row["EffectiveCost"]) if row["EffectiveCost"] is not None else None,
                    "ResourceId": row["ResourceId"] or "",
                    "Service": row["ServiceName"] or "",
                    "ChargeCategory": row["ChargeCategory"] or "",
                }


def archive(root, source_path, expected_hash, expected_bytes):
    if source_path.stat().st_size != expected_bytes or digest(source_path) != expected_hash:
        raise ValueError("Source hash or byte count mismatch")
    relative = Path("bronze") / expected_hash / source_path.name
    destination = root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        with source_path.open("rb") as source, destination.open("xb") as target:
            shutil.copyfileobj(source, target)
    if digest(destination) != expected_hash:
        raise ValueError("Bronze hash mismatch")
    return relative


def verified_version(root, version_id):
    if not re.fullmatch(r"[0-9a-f]{64}", version_id):
        raise ValueError("Invalid version ID")
    directory = root / "versions" / version_id
    manifest = read_json(directory / "manifest.json")
    selection = read_json(directory / "selection.json")
    identity = {"adapterVersion": ADAPTER_VERSION, "selection": selection}
    if hashlib.sha256(encode(identity)).hexdigest() != version_id or manifest["versionId"] != version_id:
        raise ValueError("Version identity mismatch")
    required = {"silver.parquet", "gold_daily.parquet", "gold_monthly.parquet", "validation.json", "selection.json"}
    if set(manifest["artifacts"]) != required:
        raise ValueError("Incomplete version artifacts")
    for filename, expected in manifest["artifacts"].items():
        if Path(filename).name != filename or digest(directory / filename) != expected:
            raise ValueError("Version artifact hash mismatch")
    for source in manifest["sources"]:
        bronze_path = (root / source["bronzePath"]).resolve()
        if not bronze_path.is_relative_to(root / "bronze") or digest(bronze_path) != source["sha256"]:
            raise ValueError("Bronze hash mismatch")
    return manifest


def event(root, status, **details):
    stamp = datetime.now(timezone.utc).isoformat()
    immutable_json(root / "ledger" / f"{uuid.uuid4().hex}.json", {"status": status, "atUtc": stamp, **details})


def decimal_schema(rows, fields):
    values = [row[field] for row in rows for field in fields if row[field] is not None]
    scale = max((max(0, -value.as_tuple().exponent) for value in values), default=0)
    if any(max(1, value.adjusted() + 1) + scale > 38 for value in values):
        raise ValueError("Exact amounts exceed Decimal128(38); quarantine rather than round")
    return arrow.decimal128(38, scale)


def write_table(path, rows, money_fields):
    decimal_type = decimal_schema(rows, money_fields)
    fields = [(name, decimal_type if name in money_fields else arrow.string()) for name in rows[0]]
    parquet.write_table(arrow.Table.from_pylist(rows, schema=arrow.schema(fields)), path)


def build(config_path, root):
    config_path, root = Path(config_path).resolve(), Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    with (root / "writer.lock").open("x"):
        pass
    try:
        with localcontext() as context:
            context.prec = 100
            return build_locked(config_path, root)
    finally:
        (root / "writer.lock").unlink()


def build_locked(config_path, root):
    version_id = None
    try:
        config = read_json(config_path)
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", config["customerId"]):
            raise ValueError("Invalid customer identifier")
        owner_path = root / "customer.json"
        immutable_json(owner_path, {"customerId": config["customerId"]})
        snapshots = config["snapshots"]
        periods = [snapshot["period"] for snapshot in snapshots]
        if not periods or len(periods) != len(set(periods)):
            raise ValueError("Select exactly one complete snapshot per period")
        identity = {"adapterVersion": ADAPTER_VERSION, "selection": config}
        version_id = hashlib.sha256(encode(identity)).hexdigest()
        sources, silver, coverage = [], [], []
        for evidence in config.get("evidence", []):
            relative = archive(root, (config_path.parent / evidence["path"]).resolve(), evidence["sha256"], evidence["bytes"])
            sources.append({"bronzePath": relative.as_posix(), "sha256": evidence["sha256"], "role": evidence["role"]})
        daily = defaultdict(lambda: {"billed": Decimal(0), "effective": Decimal(0), "effectiveKnown": True, "rows": 0})
        monthly = []
        for snapshot in sorted(snapshots, key=lambda entry: entry["period"]):
            period = snapshot["period"]
            month_start = date.fromisoformat(f"{period}-01")
            if snapshot["adapter"] not in ADAPTERS or snapshot["status"] not in {"Closed", "Provisional"}:
                raise ValueError("Unknown adapter or financial status")
            if not snapshot["observedAtUtc"] or not snapshot["runId"] or not snapshot["parts"]:
                raise ValueError("Missing source observation, run ID or parts")
            if snapshot["partCount"] != len(snapshot["parts"]):
                raise ValueError("Incomplete snapshot manifest")
            if len({part["path"] for part in snapshot["parts"]}) != len(snapshot["parts"]):
                raise ValueError("Repeated snapshot part")
            cost, effective, effective_known, count = Decimal(0), Decimal(0), True, 0
            observed_days = set()
            for part_index, part in enumerate(snapshot["parts"]):
                source_path = (config_path.parent / part["path"]).resolve()
                expected_hash = part["sha256"].lower()
                if source_path.stat().st_size != part["bytes"] or digest(source_path) != expected_hash:
                    raise ValueError(f"Source hash or byte count mismatch: {period}")
                if schema_digest(source_path, snapshot["adapter"]) != snapshot["schemaSha256"]:
                    raise ValueError(f"Source schema mismatch: {period}")
                bronze_path = archive(root, source_path, expected_hash, part["bytes"])
                destination = root / bronze_path
                sources.append({"period": period, "runId": snapshot["runId"], "bronzePath": bronze_path.as_posix(), "sha256": expected_hash})
                part_count = 0
                for ordinal, row in enumerate(source_rows(destination, snapshot["adapter"]), start=1):
                    subscription = row["SubscriptionId"].casefold().removeprefix("/subscriptions/")
                    if subscription != config["subscriptionId"].casefold() or row["Currency"] != config["currency"]:
                        raise ValueError(f"Source scope or currency mismatch: {period}")
                    row["PeriodBasis"] = "SourceBillingPeriod"
                    if row["PeriodStart"] is None and snapshot["adapter"] == "actualcost-2021-10-01":
                        if not row["ChargeDate"].startswith(f"{period}-"):
                            raise ValueError(f"Charge date conflicts with selected manifest: {period}")
                        row["PeriodStart"] = month_start.isoformat()
                        row["PeriodBasis"] = "SelectedManifestAndChargeDate"
                    if row["PeriodStart"] != month_start.isoformat():
                        raise ValueError(f"Source billing period mismatch: {period}")
                    cost += row["BilledCost"]
                    if row["EffectiveCost"] is None:
                        effective_known = False
                    else:
                        effective += row["EffectiveCost"]
                    observed_days.add(row["ChargeDate"])
                    count += 1
                    part_count += 1
                    row.update({"CustomerId": config["customerId"], "Period": period, "SourceRunId": snapshot["runId"], "SourceHash": expected_hash,
                                "SourceRowId": f"{snapshot['runId']}:{part_index}:{ordinal}", "Adapter": snapshot["adapter"], "AdapterVersion": ADAPTER_VERSION,
                                "ExactBilledCost": str(row["BilledCost"]), "ExactEffectiveCost": str(row["EffectiveCost"]) if row["EffectiveCost"] is not None else None,
                                "Attribution": "Unassigned", "FinancialStatus": snapshot["status"]})
                    silver.append(row)
                    bucket = daily[(period, row["ChargeDate"])]
                    bucket["billed"] += row["BilledCost"]
                    bucket["rows"] += 1
                    if row["EffectiveCost"] is None:
                        bucket["effectiveKnown"] = False
                    else:
                        bucket["effective"] += row["EffectiveCost"]
                if part_count != part["rows"]:
                    raise ValueError(f"Part row count mismatch: {period}")
            expected = number(snapshot["nativeCost"])
            delta = cost - expected
            if count != snapshot["rows"] or not count or abs(delta) > TOLERANCE:
                raise ValueError(f"Row count or native reconciliation failed: {period}")
            monthly.append({"CustomerId": config["customerId"], "Period": period, "Currency": config["currency"], "BilledCost": cost,
                            "EffectiveCost": effective if effective_known else None, "NativeCost": expected, "ReconciliationDelta": delta,
                            "Rows": str(count), "Status": snapshot["status"], "SourceRunId": snapshot["runId"], "ObservedAtUtc": snapshot["observedAtUtc"]})
            charge_days = sum(charge_day.startswith(period) for charge_day in observed_days)
            coverage.append({"period": period, "observedChargeDays": charge_days, "calendarDaysWithoutRows": calendar.monthrange(month_start.year, month_start.month)[1] - charge_days,
                             "status": snapshot["status"], "observedAtUtc": snapshot["observedAtUtc"], "missingDayMeaning": "Unknown; not evidence of zero spend or a paused environment"})
        if sum((row["BilledCost"] for row in silver), Decimal(0)) != sum((row["BilledCost"] for row in monthly), Decimal(0)):
            raise ValueError("Silver/Gold cost conservation failed")
        directory = root / "versions" / version_id
        if (directory / "manifest.json").exists():
            verified_version(root, version_id)
            return {"versionId": version_id, "status": "Validated replay", "rows": len(silver)}
        completed_directory = directory
        directory = root / "staging" / f"{version_id}-{uuid.uuid4().hex}"
        directory.mkdir(parents=True, exist_ok=False)
        gold_daily = [{"Period": period, "ChargeDate": charge_day, "Currency": config["currency"], "BilledCost": bucket["billed"],
                       "EffectiveCost": bucket["effective"] if bucket["effectiveKnown"] else None, "Rows": str(bucket["rows"])}
                      for (period, charge_day), bucket in sorted(daily.items())]
        write_table(directory / "silver.parquet", silver, {"BilledCost", "EffectiveCost"})
        write_table(directory / "gold_daily.parquet", gold_daily, {"BilledCost", "EffectiveCost"})
        write_table(directory / "gold_monthly.parquet", monthly, {"BilledCost", "EffectiveCost", "NativeCost", "ReconciliationDelta"})
        summary = {"customerId": config["customerId"], "versionId": version_id, "adapterVersion": ADAPTER_VERSION, "rows": len(silver),
                   "exactBilledCost": str(sum((row["BilledCost"] for row in monthly), Decimal(0))), "currency": config["currency"],
                   "months": [{name: str(value) if isinstance(value, Decimal) else value for name, value in row.items()} for row in monthly], "coverage": coverage}
        immutable_json(directory / "validation.json", summary)
        immutable_json(directory / "selection.json", config)
        artifacts = {path.name: digest(path) for path in directory.iterdir() if path.is_file()}
        immutable_json(directory / "manifest.json", {"versionId": version_id, "customerId": config["customerId"], "sources": sources, "artifacts": artifacts})
        completed_directory.parent.mkdir(parents=True, exist_ok=True)
        directory.rename(completed_directory)
        verified_version(root, version_id)
        event(root, "Validated", versionId=version_id, rows=len(silver))
        return {"versionId": version_id, "status": "Validated candidate", "rows": len(silver), "exactBilledCost": summary["exactBilledCost"]}
    except Exception as error:
        event(root, "Quarantined", versionId=version_id, errorType=type(error).__name__, message=str(error))
        raise


def promote(root, version_id, actor, reason, expected_current):
    root = Path(root).resolve()
    if not actor.strip() or len(reason.strip()) < 10:
        raise ValueError("Manual promotion requires an actor and a meaningful reason")
    with (root / "writer.lock").open("x"):
        pass
    try:
        verified_version(root, version_id)
        pointer = root / "published.json"
        current = read_json(pointer)["versionId"] if pointer.exists() else "none"
        if current != expected_current:
            raise ValueError("Published version changed; review before promotion")
        event(root, "PromotionApproved", versionId=version_id, previousVersionId=current, actor=actor, reason=reason)
        temporary = root / f".publish-{uuid.uuid4().hex}.json"
        immutable_json(temporary, {"versionId": version_id, "actor": actor, "reason": reason, "previousVersionId": current})
        os.replace(temporary, pointer)
    finally:
        (root / "writer.lock").unlink()


def main():
    parser = argparse.ArgumentParser(description="Isolated, manual FinOps financial foundation")
    parser.add_argument("--root", required=True, type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    build_parser = commands.add_parser("build")
    build_parser.add_argument("--selection", required=True, type=Path)
    promote_parser = commands.add_parser("promote")
    promote_parser.add_argument("--version", required=True)
    promote_parser.add_argument("--expected-current", required=True)
    promote_parser.add_argument("--actor", required=True)
    promote_parser.add_argument("--reason", required=True)
    args = parser.parse_args()
    if args.command == "build":
        result = build(args.selection, args.root)
    else:
        promote(args.root, args.version, args.actor, args.reason, args.expected_current)
        result = {"status": "Promoted", "versionId": args.version}
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()