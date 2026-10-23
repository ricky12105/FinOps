"""Prepare local CSV billing tables and business-context attribution for the prototype PBIP model.

Reads FOCUS Parquet snapshots from `.data/raw`, applies complete source business tags or
effective-dated fallback mappings from `config/demo-attribution.json`, verifies four-decimal
rounding bridges against exact source totals, and outputs `billing.csv`, `context.csv`,
`snapshots.csv`, `date.csv`, and `source-audit.json` to `.data/processed`.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_EVEN
from pathlib import Path
from typing import Any

import pyarrow.parquet as parquet

from finops_config import DEFAULT_SUBSCRIPTION_ID, load_customer_config, resource_relative_key


ROOT: Path = Path(__file__).resolve().parents[1]
PERIODS: set[str] = {"2026-06", "2026-07", "2026-08", "2026-09"}
PLATFORMS: dict[str, str] = {"Microsoft.Fabric": "Fabric", "Azure Databricks": "Databricks", "Microsoft Purview": "Purview"}
BUSINESS_FIELDS: tuple[str, ...] = ("businessUnit", "initiative", "project", "tool")


def money(value: Decimal) -> Decimal:
    """Quantize a Decimal amount to 4 decimal places using half-to-even banker's rounding."""
    return value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)


def read_mapping(path: Path) -> tuple[dict[str, Any], dict[str, dict[str, str]]]:
    """Load and validate the business-context attribution mapping file.

    Raises:
        ValueError: If duplicate resource IDs or incomplete business fields are detected.
    """
    config = json.loads(path.read_text(encoding="utf-8"))
    mapping: dict[str, dict[str, str]] = {}
    for entry in config["mappings"]:
        key = entry["resourceId"].casefold()
        rel_key = resource_relative_key(key)
        if key in mapping or not all(entry.get(field) for field in BUSINESS_FIELDS):
            raise ValueError("Duplicate or incomplete attribution mapping")
        mapping[key] = entry
        mapping[rel_key] = entry
    return config, mapping


def attribution(row: dict[str, Any], config: dict[str, Any], mapping: dict[str, dict[str, str]]) -> list[str]:
    """Resolve Business Unit, Initiative, Project, Tool, and Provenance for a single charge row.

    Complete source tags (`BusinessUnit`, `Initiative`, `Project`, `Tool`) take precedence over
    effective-dated fallback mappings. Unknown or out-of-window resources return `Unassigned`.
    """
    tags = {key.casefold(): value for key, value in json.loads(row["Tags"] or "{}").items()}
    if all(tags.get(field.casefold()) for field in BUSINESS_FIELDS):
        return [str(tags[field.casefold()]) for field in BUSINESS_FIELDS] + ["Source tags"]
    raw_rid = (row["ResourceId"] or "").casefold()
    entry = mapping.get(raw_rid) or mapping.get(resource_relative_key(raw_rid))
    charge_date = row["ChargePeriodStart"].date().isoformat()
    if entry and config["validFrom"] <= charge_date < config["validToExclusive"]:
        return [entry[field] for field in BUSINESS_FIELDS] + ["Demo mapping"]
    return ["Unassigned"] * len(BUSINESS_FIELDS) + ["Unassigned"]


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    """Write a non-empty list of dictionaries to a UTF-8 CSV file."""
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def load_snapshots(subscription_id: str | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Load the latest FOCUS Parquet export generation per period and verify row/byte counts."""
    expected_sub = (subscription_id or load_customer_config().get("subscriptionId") or DEFAULT_SUBSCRIPTION_ID).lower()
    generations: dict[str, tuple[Path, dict[str, Any]]] = {}
    for manifest_path in sorted((ROOT / ".data/raw").rglob("manifest.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        period = manifest["runInfo"]["startDate"][:7]
        if period not in PERIODS:
            continue
        current = generations.get(period)
        if current is None or manifest["runInfo"]["submittedTime"] > current[1]["runInfo"]["submittedTime"]:
            generations[period] = (manifest_path, manifest)
    if set(generations) != PERIODS:
        raise ValueError("Expected exactly June through September 2026")
    rows: list[dict[str, Any]] = []
    audit: list[dict[str, Any]] = []
    for period, (manifest_path, manifest) in sorted(generations.items()):
        if len(manifest["blobs"]) != manifest["blobCount"]:
            raise ValueError(f"Incomplete manifest: {period}")
        batch: list[dict[str, Any]] = []
        for blob in manifest["blobs"]:
            source_path = manifest_path.parent / Path(blob["blobName"]).name
            if source_path.stat().st_size != blob["byteCount"]:
                raise ValueError(f"File size mismatch: {source_path}")
            part = parquet.read_table(source_path).to_pylist()
            if len(part) != blob["dataRowCount"]:
                raise ValueError(f"Row count mismatch: {source_path}")
            batch.extend(part)
        if len(batch) != manifest["dataRowCount"]:
            raise ValueError(f"Manifest row count mismatch: {period}")
        for row in batch:
            row_sub = row["SubAccountId"].lower().removeprefix("/subscriptions/")
            if (expected_sub != DEFAULT_SUBSCRIPTION_ID and row_sub != expected_sub) or row["BillingCurrency"] != "USD":
                raise ValueError("Unexpected subscription or currency")
            if row["BillingPeriodStart"].strftime("%Y-%m") != period:
                raise ValueError("Billing period does not match snapshot")
            if not isinstance(row["BilledCost"], Decimal):
                raise ValueError("BilledCost must be a non-null decimal")
            row["SnapshotPeriod"] = period
            row["SourceRunId"] = manifest["runInfo"]["runId"]
            row["SnapshotSubmitted"] = manifest["runInfo"]["submittedTime"]
        audit.append(
            {
                "period": period,
                "rows": len(batch),
                "billedCost": str(sum((row["BilledCost"] for row in batch), Decimal(0))),
                "runId": manifest["runInfo"]["runId"],
                "submittedTime": manifest["runInfo"]["submittedTime"],
            }
        )
        rows.extend(batch)
    return rows, audit


def main(subscription_id: str | None = None) -> None:
    """Execute the local billing preparation pipeline and verify cost/row conservation."""
    rows, audit = load_snapshots(subscription_id=subscription_id)
    config, mapping = read_mapping(ROOT / "config/demo-attribution.json")
    output = ROOT / ".data/processed"
    output.mkdir(parents=True, exist_ok=True)
    totals: dict[tuple[str, str, str], Decimal] = defaultdict(Decimal)
    contexts: dict[tuple[str, ...], dict[str, Any]] = {}
    billing = []
    modeled_totals = defaultdict(Decimal)
    for ordinal, row in enumerate(rows, start=1):
        business_unit, initiative, project, tool, provenance = attribution(row, config, mapping)
        platform = PLATFORMS.get(row["ServiceName"], "Other / Unmapped")
        resource_id = (row["ResourceId"] or "").casefold()
        context_key = (resource_id, row["ServiceName"], business_unit, initiative, project, tool, provenance)
        if context_key not in contexts:
            contexts[context_key] = {
                "ContextKey": len(contexts) + 1,
                "Resource": row["ResourceName"] or "Unspecified resource",
                "ResourceId": resource_id,
                "ResourceGroup": row["x_ResourceGroupName"] or "Unspecified",
                "Service": row["ServiceName"], "Platform": platform,
                "BusinessUnit": business_unit, "Initiative": initiative,
                "Project": project, "Tool": tool, "Attribution": provenance,
                "CostScope": "Direct service" if platform != "Other / Unmapped" else "Other / unattributed infrastructure",
            }
        cost = money(row["BilledCost"])
        billing.append({
            "RowId": ordinal, "ContextKey": contexts[context_key]["ContextKey"],
            "Date": row["ChargePeriodStart"].date().isoformat(), "Period": row["SnapshotPeriod"],
            "Meter": row["x_SkuMeterName"] or "Unspecified meter",
            "ChargeCategory": row["ChargeCategory"], "ChargeClass": row["ChargeClass"] or "Regular",
            "BilledCost": str(cost), "ExactBilledCost": str(row["BilledCost"]),
            "SourceRunId": row["SourceRunId"],
        })
        totals[(platform, initiative, provenance)] += row["BilledCost"]
        modeled_totals[row["SnapshotPeriod"]] += cost
    if len(billing) != sum(snapshot["rows"] for snapshot in audit):
        raise ValueError("Attribution changed the number of billing rows")
    if sum(totals.values(), Decimal(0)) != sum((Decimal(snapshot["billedCost"]) for snapshot in audit), Decimal(0)):
        raise ValueError("Attribution changed source costs")
    snapshots = []
    for snapshot in audit:
        period = snapshot["period"]
        exact_total = Decimal(snapshot["billedCost"])
        delta = modeled_totals[period] - money(exact_total)
        snapshot["modelBilledCost"] = str(modeled_totals[period])
        snapshot["roundingDelta"] = str(delta)
        if abs(delta) > Decimal("0.10"):
            raise ValueError(f"Model rounding exceeds USD 0.10 per month: {period}")
        snapshots.append({"Period": period, "SourceRows": snapshot["rows"], "SourceBilledCost": str(money(exact_total)), "ModelBilledCost": str(modeled_totals[period]), "RoundingDelta": str(delta), "Status": "Provisional MTD" if period == "2026-09" else "Complete month snapshot", "SubmittedUTC": snapshot["submittedTime"], "RunId": snapshot["runId"]})
    dates = []
    current = date(2026, 6, 1)
    while current < date(2026, 10, 1):
        dates.append({"Date": current.isoformat(), "Month": current.strftime("%Y-%m"), "MonthName": current.strftime("%b %Y")})
        current += timedelta(days=1)
    for filename, data in [("billing.csv", billing), ("context.csv", list(contexts.values())), ("snapshots.csv", snapshots), ("date.csv", dates)]:
        write_csv(output / filename, data)
    (output / "source-audit.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    print(json.dumps({"snapshots": audit, "totalRows": len(rows), "contexts": len(contexts), "attributionConservation": "PASS", "platformInitiatives": [{"platform": key[0], "initiative": key[1], "attribution": key[2], "exactBilledCost": str(value)} for key, value in totals.items()]}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Prepare local CSV billing tables and business-context attribution.")
    parser.add_argument("--subscription-id", default=None, help="Optional Azure Subscription GUID override")
    cli_args = parser.parse_args()
    main(subscription_id=cli_args.subscription_id)