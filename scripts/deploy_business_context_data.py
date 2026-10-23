"""Seed the Fabric SQL Database (`ZavaFinOpsBusinessContext`) from validated local FinOps snapshots.

Populates `dbo.DemoContextSource`, `dbo.BusinessContextResource`, `dbo.BusinessContextOption`,
`dbo.BusinessContextGrant`, `dbo.BusinessContextConfiguration`, `dbo.DemoBilling`,
`dbo.DemoCalendar`, and `dbo.DemoSnapshot` within a single transaction, refusing to overwrite
if any manual assignments already exist in `dbo.BusinessContextAssignment`.
"""

from __future__ import annotations

import argparse
import csv
import json
import uuid
from collections import OrderedDict
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import mssql_python


def csv_rows(path: Path) -> list[dict[str, str]]:
    """Read all rows from a UTF-8 CSV file as a list of string dictionaries."""
    with path.open(encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def load_demo_data(
    connection: Any,
    processed_dir: Path,
    mapping_file: Path,
    tenant_id: str,
    actor_id: str,
) -> dict[str, int]:
    """Load prepared CSV snapshots and controlled business-context options into SQL Server."""
    contexts = csv_rows(processed_dir / "context.csv")
    billing = csv_rows(processed_dir / "billing.csv")
    calendar = csv_rows(processed_dir / "date.csv")
    snapshots = csv_rows(processed_dir / "snapshots.csv")
    mapping = json.loads(mapping_file.read_text(encoding="utf-8"))
    options: OrderedDict[tuple[str, str, str, str], str] = OrderedDict()
    for item in mapping["mappings"]:
        values = (item["businessUnit"], item["initiative"], item["project"], item["tool"])
        option_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "zava-finops:" + json.dumps(values, separators=(",", ":"))))
        options[values] = option_id

    if len({row["ContextKey"] for row in contexts}) != len(contexts):
        raise ValueError("Context keys must be unique.")
    if len({row["ResourceId"].casefold() for row in contexts}) != len(contexts):
        raise ValueError("Resource IDs must be unique for this demo seed.")
    if not all(row["ResourceId"] for row in contexts):
        raise ValueError("Every demo context must have a resource ID.")

    cursor = connection.cursor()
    cursor.execute("SELECT COUNT_BIG(*) FROM dbo.BusinessContextAssignment")
    if cursor.fetchone()[0]:
        raise RuntimeError("Assignments already exist. Refusing to replace resource/catalog seed data.")

    for table in ("BusinessContextGrant", "BusinessContextOption", "BusinessContextResource", "DemoBilling", "DemoCalendar", "DemoSnapshot", "DemoContextSource"):
        cursor.execute(f"DELETE FROM dbo.{table}")

    cursor.executemany(
        "INSERT dbo.DemoContextSource (TenantId, ContextKey, ResourceKey, Resource, ResourceId, ResourceGroup, Service, Platform, BusinessUnit, Initiative, Project, Tool, Attribution, CostScope) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
            tenant_id, int(row["ContextKey"]), f"CTX-{row['ContextKey']}", row["Resource"], row["ResourceId"],
                row["ResourceGroup"], row["Service"], row["Platform"], row["BusinessUnit"],
                row["Initiative"], row["Project"], row["Tool"], row["Attribution"], row["CostScope"],
            )
            for row in contexts
        ],
    )
    cursor.executemany(
        "INSERT dbo.BusinessContextResource (ResourceKey, TenantId, ResourceId, DisplayName, IsAssignable, HasCompleteSourceTags, LastSynchronizedAt) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (
                f"CTX-{row['ContextKey']}", tenant_id, row["ResourceId"], row["Resource"],
                row["Attribution"] == "Unassigned", row["Attribution"] == "Source tags",
                datetime.now(timezone.utc).replace(tzinfo=None),
            )
            for row in contexts
        ],
    )
    cursor.executemany(
        "INSERT dbo.BusinessContextOption (ContextOptionId, TenantId, BusinessUnit, Initiative, Project, Tool, IsDemoFixture) VALUES (?, ?, ?, ?, ?, ?, 1)",
        [(option_id, tenant_id, *values) for values, option_id in options.items()],
    )
    eligible_keys = [f"CTX-{row['ContextKey']}" for row in contexts if row["Attribution"] == "Unassigned"]
    grant_date = date.today()
    cursor.executemany(
        "INSERT dbo.BusinessContextGrant (TenantId, ActorObjectId, ResourceKey, ContextOptionId, ValidFrom) VALUES (?, ?, ?, ?, ?)",
        [(tenant_id, actor_id, resource_key, option_id, grant_date) for resource_key in eligible_keys for option_id in options.values()],
    )
    cursor.execute(
        "IF EXISTS (SELECT 1 FROM dbo.BusinessContextConfiguration WHERE TenantId = ?) "
        "UPDATE dbo.BusinessContextConfiguration SET DemoFixturesEnabled = 1, UpdatedAtUtc = SYSUTCDATETIME() WHERE TenantId = ? "
        "ELSE INSERT dbo.BusinessContextConfiguration (TenantId, DemoFixturesEnabled) VALUES (?, 1)",
        tenant_id,
        tenant_id,
        tenant_id,
    )
    cursor.executemany(
        "INSERT dbo.DemoBilling (RowId, ContextKey, Date, Period, Meter, ChargeCategory, ChargeClass, BilledCost, ExactBilledCost, SourceRunId) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                int(row["RowId"]), int(row["ContextKey"]), date.fromisoformat(row["Date"]), row["Period"],
                row["Meter"], row["ChargeCategory"], row["ChargeClass"], Decimal(row["BilledCost"]),
                row["ExactBilledCost"], row["SourceRunId"],
            )
            for row in billing
        ],
    )
    cursor.executemany(
        "INSERT dbo.DemoCalendar (Date, Month, MonthName) VALUES (?, ?, ?)",
        [(date.fromisoformat(row["Date"]), row["Month"], row["MonthName"]) for row in calendar],
    )
    cursor.executemany(
        "INSERT dbo.DemoSnapshot (Period, SourceRows, SourceBilledCost, ModelBilledCost, RoundingDelta, Status, SubmittedUTC, RunId) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                row["Period"], int(row["SourceRows"]), Decimal(row["SourceBilledCost"]),
                Decimal(row["ModelBilledCost"]), Decimal(row["RoundingDelta"]), row["Status"],
                row["SubmittedUTC"], row["RunId"],
            )
            for row in snapshots
        ],
    )
    connection.commit()

    cursor.execute("SELECT COUNT_BIG(*) FROM dbo.Billing")
    observed_charge_rows = int(cursor.fetchone()[0])
    cursor.execute("SELECT COUNT_BIG(*) FROM dbo.Context")
    observed_context_rows = int(cursor.fetchone()[0])
    cursor.execute("SELECT COUNT_BIG(*) FROM dbo.BusinessContextCandidates WHERE IsAssignable = 1")
    observed_assignable_rows = int(cursor.fetchone()[0])
    cursor.close()
    expected_charge_rows = len(billing)
    expected_context_rows = len(contexts)
    expected_assignable_rows = len(eligible_keys)
    if (
        observed_charge_rows != expected_charge_rows
        or observed_context_rows < expected_context_rows
        or observed_assignable_rows != expected_assignable_rows
    ):
        raise RuntimeError("Cloud seed verification did not match the prepared snapshot counts.")
    return {
        "seededRows": expected_charge_rows,
        "contexts": expected_context_rows,
        "resourcesAllowed": expected_assignable_rows,
        "businessContextOptions": len(options),
        "actorGrants": expected_assignable_rows * len(options),
    }


def main():
    parser = argparse.ArgumentParser(description="Seed the FinOps demo SQL Database from validated local snapshots.")
    parser.add_argument("--server", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--tenant-id", required=True, type=lambda value: str(uuid.UUID(value)))
    parser.add_argument("--actor-object-id", required=True, type=lambda value: str(uuid.UUID(value)))
    parser.add_argument("--processed-dir", type=Path, default=Path(".data/processed"))
    parser.add_argument("--mapping-file", type=Path, default=Path("config/demo-attribution.json"))
    args = parser.parse_args()

    connection = mssql_python.connect(
        f"Server={args.server};Database={args.database};Authentication=ActiveDirectoryDefault;Encrypt=yes;TrustServerCertificate=no"
    )
    try:
        print(json.dumps(load_demo_data(connection, args.processed_dir, args.mapping_file, args.tenant_id, args.actor_object_id), indent=2))
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    main()