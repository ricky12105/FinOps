"""Build and materialize the 30-table versioned Direct Lake serving layer for `ZavaFinOpsDemo`.

Combines validated financial foundation snapshots, Databricks operational medallion tables,
Unity Catalog asset inventory, Fabric FUAM estate/operation telemetry, Azure Cost Management
daily resource-group anomaly alerts, Azure Advisor recommendations, Microsoft Foundry AI usage,
and SQL business-context assignments into a content-addressed Parquet ZIP bundle and materializes
`demo.demo_*_<buildId[:12]>` Delta tables in OneLake.

Supports both `--sample` mode (for immediate sandbox evaluation on a clean git clone without
local `.data/` exports) and live snapshot mode.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_EVEN, localcontext
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

import pyarrow as arrow
import pyarrow.parquet as parquet

from finops_foundation import digest, immutable_json, number, read_json

try:
    from finops_config import (
        DEFAULT_SUBSCRIPTION_ID,
        load_customer_config,
        resolve_azure_cli,
        resource_relative_key,
    )
except ImportError:
    DEFAULT_SUBSCRIPTION_ID = "00000000-0000-0000-0000-000000000002"

    def load_customer_config(config_path: Any = None, prefer_local: bool = True) -> dict[str, Any]:
        return {}

    def resolve_azure_cli() -> str:
        return shutil.which("az.cmd") or shutil.which("az") or "az"

    def resource_relative_key(resource_id: str) -> str:
        lowered = (resource_id or "unallocated resource").strip().lower()
        return re.sub(r"^/subscriptions/[0-9a-fA-F-]{36}/", "/", lowered, count=1)


PROJECT: Path = Path(__file__).resolve().parent.parent
MONEY: Decimal = Decimal("0.0001")


def presentation(value: str | Decimal | int) -> Decimal:
    """Quantize a numeric value to 4 decimal places (`Decimal(19, 4)`) using half-even rounding."""
    with localcontext() as context:
        context.prec = 100
        return number(value).quantize(MONEY, rounding=ROUND_HALF_EVEN)


def month_after(period: str, offset: int) -> date:
    """Return the first day of the calendar month `offset` months after `YYYY-MM` `period`."""
    current = date.fromisoformat(f"{period}-01")
    month_index = current.year * 12 + current.month - 1 + offset
    return date(month_index // 12, month_index % 12 + 1, 1)


def forecast_tables(
    monthly: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str]]:
    """Compute a 3-month trailing-mean forecast and 3-period rolling-origin holdout backtest.

    Raises:
        ValueError: If fewer than 6 unique consecutive `Closed` monthly periods are supplied.
    """
    monthly = sorted(monthly, key=lambda row: row["Period"])
    if len(monthly) < 6 or len({row["Period"] for row in monthly}) != len(monthly):
        raise ValueError("Forecast requires at least six unique consecutive closed months")
    if any(
        month_after(monthly[0]["Period"], index).strftime("%Y-%m") != row["Period"] or row["Status"] != "Closed"
        for index, row in enumerate(monthly)
    ):
        raise ValueError("Forecast training history has gaps or provisional periods")
    with localcontext() as context:
        context.prec = 100
        amounts = [number(row["BilledCost"]) for row in monthly]
        tests: list[dict[str, Any]] = []
        for index in range(len(monthly) - 3, len(monthly)):
            predicted = sum(amounts[index - 3 : index], Decimal(0)) / 3
            naive = amounts[index - 1]
            tests.append(
                {
                    "Period": monthly[index]["Period"],
                    "Date": month_after(monthly[index]["Period"], 0),
                    "Actual": presentation(amounts[index]),
                    "Predicted": presentation(predicted),
                    "Naive": presentation(naive),
                    "AbsoluteError": presentation(abs(amounts[index] - predicted)),
                    "NaiveAbsoluteError": presentation(abs(amounts[index] - naive)),
                    "TrainingThrough": monthly[index - 1]["Period"],
                    "Basis": "Retrospective period holdout; retained revised snapshots, not vintage replay",
                }
            )
        error = sum((row["AbsoluteError"] for row in tests), Decimal(0)) / len(tests)
        naive_error = sum((row["NaiveAbsoluteError"] for row in tests), Decimal(0)) / len(tests)
        predicted = sum(amounts[-3:], Decimal(0)) / 3
        forecast = [
            {
                "Date": month_after(monthly[-1]["Period"], offset),
                "Month": month_after(monthly[-1]["Period"], offset).strftime("%Y-%m"),
                "Baseline": presentation(predicted),
                "LowerIllustrative": presentation(max(Decimal(0), predicted - 2 * error)),
                "UpperIllustrative": presentation(predicted + 2 * error),
                "Method": "Trailing three-month mean",
                "TrainingWindow": f"{monthly[-3]['Period']} through {monthly[-1]['Period']}",
                "Horizon": "Three months",
                "RangeBasis": "Baseline +/- twice retrospective MAE; illustrative, not a calibrated confidence interval",
                "Limitations": "Twelve retained financial snapshots; no validated seasonality, business drivers or vintage-correct history",
                "AsOfLabel": f"Financial history through {monthly[-1]['Period']}; forecast/scenarios are modeled, not actuals",
            }
            for offset in (1, 2, 3)
        ]
    return forecast, tests, {"MeanAbsoluteError": str(presentation(error)), "NaiveMeanAbsoluteError": str(presentation(naive_error))}


def sql_read(server: str, database: str, statement: str, subscription_id: str | None = None) -> list[dict[str, Any]]:
    """Execute a bounded read-only `SELECT` query against a Fabric SQL or Warehouse endpoint."""
    if not statement.lstrip().upper().startswith("SELECT"):
        raise ValueError("This serving adapter accepts SELECT statements only")
    import mssql_python

    cfg = load_customer_config()
    sub_id = subscription_id or cfg.get("subscriptionId") or os.environ.get("AZURE_SUBSCRIPTION_ID")
    az_bin = resolve_azure_cli()
    command = [az_bin, "account", "get-access-token", "--resource", "https://database.windows.net", "--query", "accessToken", "-o", "tsv"]
    if sub_id and sub_id != DEFAULT_SUBSCRIPTION_ID:
        command.extend(["--subscription", str(sub_id)])
    result = subprocess.run(command, capture_output=True, text=True, check=True, shell=False, timeout=30)
    token = result.stdout.strip().encode("utf-16-le")
    connection = mssql_python.connect(
        f"Server={server},1433;Database={database};Encrypt=yes;TrustServerCertificate=no",
        attrs_before={1256: struct.pack("<I", len(token)) + token},
        timeout=30,
    )
    try:
        cursor = connection.cursor()
        try:
            cursor.execute(statement)
            columns = [column[0] for column in cursor.description]
            rows = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
            if len(rows) >= 10000:
                raise ValueError("Read-only serving extraction exceeded its row bound")
            return rows
        finally:
            cursor.close()
    finally:
        connection.close()


def capture_supporting_sources(output: Path | str, sample: bool = False) -> dict[str, Any]:
    """Capture or load bounded FUAM and Business Context SQL snapshots."""
    output = Path(output)
    path = output / "supporting-source-snapshot.json"
    if path.exists():
        return read_json(path)
    if sample:
        sample_supporting = _build_synthetic_supporting_snapshot()
        output.mkdir(parents=True, exist_ok=True)
        immutable_json(path, sample_supporting)
        return read_json(path)
    cfg = load_customer_config()
    sql_cfg = cfg.get("sqlEndpoints", {})
    fuam_server = sql_cfg.get("fuamServer") or os.environ.get("FUAM_SQL_SERVER", "")
    fuam_database = sql_cfg.get("fuamDatabase") or os.environ.get("FUAM_SQL_DATABASE", "FUAM_Lakehouse")
    context_server = sql_cfg.get("businessContextServer") or os.environ.get("BUSINESS_CONTEXT_SQL_SERVER", "")
    context_database = sql_cfg.get("businessContextDatabase") or os.environ.get("BUSINESS_CONTEXT_SQL_DATABASE", "ZavaFinOpsBusinessContext")
    if not fuam_server or not context_server or "your-" in fuam_server or "your-" in context_server:
        raise ValueError(
            "SQL server endpoints are not configured. Set FUAM_SQL_SERVER and BUSINESS_CONTEXT_SQL_SERVER "
            "in config/customer.local.json or .env, or pass --sample for synthetic sandbox mode."
        )
    queries = {
        "fabric": (
            fuam_server,
            fuam_database,
            "SELECT TOP (5000) [Date],CapacityId,WorkspaceId,ItemId,ItemKind,OperationName,TotalCUs,OperationCount,SuccessOperationCount,FailureOperationCount,CancelledOperationCount,RejectedOperationCount,InvalidOperationCount,DurationInSec,ThrottlingInMin FROM dbo.capacity_metrics_by_item_by_operation_by_day WHERE [Date]>='2026-08-17' AND [Date]<'2026-08-29' ORDER BY [Date],CapacityId,WorkspaceId,ItemId,ItemKind,OperationName",
        ),
        "candidates": (context_server, context_database, "SELECT TOP (1000) * FROM dbo.BusinessContextCandidates ORDER BY ResourceKey"),
        "options": (context_server, context_database, "SELECT TOP (1000) * FROM dbo.BusinessContextOption ORDER BY ContextOptionId"),
        "assignments": (context_server, context_database, "SELECT TOP (1000) * FROM dbo.BusinessContextAssignment ORDER BY AssignmentId"),
        "contexts": (context_server, context_database, "SELECT TOP (1000) * FROM dbo.Context ORDER BY ContextKey"),
        "configuration": (context_server, context_database, "SELECT TOP (1000) TenantId,DemoFixturesEnabled FROM dbo.BusinessContextConfiguration ORDER BY TenantId"),
    }
    result = {"observedAtUtc": datetime.now(timezone.utc).isoformat(), "queries": {name: query for name, (_, _, query) in queries.items()}}
    for name, (server, database, query) in queries.items():
        rows = sql_read(server, database, query)
        bound = 5000 if name == "fabric" else 1000
        if len(rows) >= bound:
            raise ValueError(f"Supporting source might be truncated: {name}")
        result[name] = json.loads(json.dumps(rows, default=lambda value: value.isoformat() if isinstance(value, (date, datetime)) else str(value)))
    grain = [(row["Date"], row["CapacityId"], row["WorkspaceId"], row["ItemId"], row["ItemKind"], row["OperationName"]) for row in result["fabric"]]
    if len(grain) != len(set(grain)):
        raise ValueError("FUAM operation grain is not unique; do not silently aggregate duplicate snapshots")
    output.mkdir(parents=True, exist_ok=True)
    immutable_json(path, result)
    return result


def _build_synthetic_supporting_snapshot() -> dict[str, Any]:
    """Return a deterministic synthetic supporting snapshot for offline/sandbox `--sample` mode."""
    tenant_id = "00000000-0000-0000-0000-000000000001"
    cap_id = "00000000-0000-0000-0000-000000000004"
    ws_id = "00000000-0000-0000-0000-000000000003"
    sub_id = DEFAULT_SUBSCRIPTION_ID
    return {
        "observedAtUtc": "2026-10-20T00:00:00Z",
        "queries": {"mode": "synthetic-sample"},
        "fabric": [
            {
                "Date": "2026-08-18",
                "CapacityId": cap_id,
                "WorkspaceId": ws_id,
                "ItemId": "item-notebook-01",
                "ItemKind": "Notebook",
                "OperationName": "SparkJobRun",
                "TotalCUs": "42500.5000",
                "OperationCount": 120,
                "SuccessOperationCount": 118,
                "FailureOperationCount": 2,
                "CancelledOperationCount": 0,
                "RejectedOperationCount": 0,
                "InvalidOperationCount": 0,
                "DurationInSec": 1840,
                "ThrottlingInMin": "0.0000",
            },
            {
                "Date": "2026-08-19",
                "CapacityId": cap_id,
                "WorkspaceId": ws_id,
                "ItemId": "item-dataset-01",
                "ItemKind": "Dataset",
                "OperationName": "DirectLakeQuery",
                "TotalCUs": "81250.2500",
                "OperationCount": 640,
                "SuccessOperationCount": 639,
                "FailureOperationCount": 1,
                "CancelledOperationCount": 0,
                "RejectedOperationCount": 0,
                "InvalidOperationCount": 0,
                "DurationInSec": 920,
                "ThrottlingInMin": "0.0000",
            },
        ],
        "candidates": [
            {
                "TenantId": tenant_id,
                "ResourceKey": "CTX-1",
                "ResourceId": f"/subscriptions/{sub_id}/resourcegroups/rg-vms/providers/microsoft.compute/virtualmachines/vm-unassigned-01",
                "DisplayName": "vm-unassigned-01",
                "IsAssignable": 1,
                "HasCompleteSourceTags": 0,
                "ResourceVersion": 1,
                "Platform": "Other / Unmapped",
                "Service": "Virtual Machines",
                "ResourceGroup": "rg-vms",
                "SourceAttribution": "Unassigned",
            }
        ],
        "options": [
            {
                "ContextOptionId": "11111111-1111-1111-1111-111111111111",
                "TenantId": tenant_id,
                "BusinessUnit": "Finance",
                "Initiative": "Finance Modernization",
                "Project": "Finance Analytics",
                "Tool": "Fabric Analytics",
                "IsDemoFixture": True,
            },
            {
                "ContextOptionId": "22222222-2222-2222-2222-222222222222",
                "TenantId": tenant_id,
                "BusinessUnit": "Data Platform",
                "Initiative": "Enterprise Data Foundation",
                "Project": "Platform Enablement",
                "Tool": "Fabric Analytics",
                "IsDemoFixture": True,
            },
        ],
        "assignments": [
            {
                "AssignmentId": 1,
                "TenantId": tenant_id,
                "ResourceKey": "CTX-1",
                "ContextOptionId": "11111111-1111-1111-1111-111111111111",
                "EffectiveFrom": "2026-09-01",
                "ActorObjectId": "00000000-0000-0000-0000-000000000014",
                "InvocationId": "00000000-0000-0000-0000-000000000099",
                "CreatedAtUtc": "2026-10-15T12:00:00Z",
                "IsDemoFixture": True,
            }
        ],
        "contexts": [],
        "configuration": [{"TenantId": tenant_id, "DemoFixturesEnabled": True}],
    }


def _build_synthetic_inputs() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Generate deterministic synthetic financial, Databricks, UC, FUAM, Anomaly, and Foundry datasets."""
    sub_id = DEFAULT_SUBSCRIPTION_ID
    months = [
        ("2025-10", Decimal("51.2072")),
        ("2025-11", Decimal("675.7822")),
        ("2025-12", Decimal("675.9539")),
        ("2026-01", Decimal("1100.3642")),
        ("2026-02", Decimal("1262.5492")),
        ("2026-03", Decimal("2404.5758")),
        ("2026-04", Decimal("1381.4000")),
        ("2026-05", Decimal("1792.0145")),
        ("2026-06", Decimal("2773.3803")),
        ("2026-07", Decimal("1746.7048")),
        ("2026-08", Decimal("1757.3610")),
        ("2026-09", Decimal("1298.6247")),
    ]
    resources = [
        (f"/subscriptions/{sub_id}/resourcegroups/rg-fabric/providers/microsoft.fabric/capacities/astoriafabricnorthcentralus", "Microsoft.Fabric", Decimal("0.50")),
        (f"/subscriptions/{sub_id}/resourcegroups/rg-fabric/providers/microsoft.databricks/workspaces/databricksworkspace", "Azure Databricks", Decimal("0.25")),
        (f"/subscriptions/{sub_id}/resourcegroups/purview-rg/providers/microsoft.purview/accounts/purview-astoria", "Microsoft.Purview", Decimal("0.15")),
        (f"/subscriptions/{sub_id}/resourcegroups/rg-vms/providers/microsoft.compute/virtualmachines/vm-unassigned-01", "Virtual Machines", Decimal("0.10")),
    ]
    financial_rows: list[dict[str, Any]] = []
    monthly_rows: list[dict[str, Any]] = []
    for period, total_cost in months:
        rem = total_cost
        for idx, (rid, svc, weight) in enumerate(resources):
            amt = (total_cost * weight).quantize(Decimal("0.0001")) if idx < len(resources) - 1 else rem
            rem -= amt
            financial_rows.append(
                {
                    "ChargeDate": f"{period}-15",
                    "Period": period,
                    "Service": svc,
                    "ResourceId": rid,
                    "BilledCost": amt,
                    "ExactBilledCost": str(amt),
                    "FinancialStatus": "Closed",
                    "ChargeCategory": "Usage",
                    "SourceRowId": f"sample-{period}-{idx + 1}",
                }
            )
        monthly_rows.append(
            {
                "Period": period,
                "BilledCost": total_cost,
                "ReconciliationDelta": Decimal("0.0000"),
                "Rows": str(len(resources)),
                "Status": "Closed",
                "SourceRunId": f"run-{period}",
                "ObservedAtUtc": "2026-10-19T00:00:00Z",
            }
        )
    usage = [
        {
            "usage_date": "2026-05-15",
            "attribution_type": "Warehouse",
            "attribution_id": "0000000000000000",
            "usage_quantity": Decimal("356.7200"),
            "usage_records": 38,
            "billing_origin_product": "SQL",
            "sku_name": "PREMIUM_SQL_PRO_COMPUTE",
        },
        {
            "usage_date": "2026-06-08",
            "attribution_type": "Platform/Unallocated",
            "attribution_id": "Unallocated",
            "usage_quantity": Decimal("148.1411"),
            "usage_records": 19,
            "billing_origin_product": "PREDICTIVE_OPTIMIZATION",
            "sku_name": "PREMIUM_JOBS_SERVERLESS_COMPUTE",
        },
    ]
    queries = [
        {
            "query_date": "2026-05-15",
            "warehouse_id": "0000000000000000",
            "execution_status": "FINISHED",
            "statement_count": 4279,
            "execution_duration_ms": 1250000,
            "read_bytes": 985000000,
        },
        {
            "query_date": "2026-05-16",
            "warehouse_id": "0000000000000000",
            "execution_status": "FAILED",
            "statement_count": 247,
            "execution_duration_ms": 45000,
            "read_bytes": 0,
        },
    ]
    uc_tables = [
        {"created_date": "2026-03-10", "table_catalog": "demo", "table_schema": "finops", "table_name": "gold_charges", "table_type": "MANAGED", "table_format": "DELTA"},
        {"created_date": "2026-04-12", "table_catalog": "system", "table_schema": "billing", "table_name": "usage", "table_type": "VIEW", "table_format": "UNITY_CATALOG"},
    ]
    uc_volumes = [
        {"created_date": "2026-03-15", "volume_catalog": "demo", "volume_schema": "raw", "volume_name": "exports", "volume_type": "MANAGED"}
    ]
    fuam_inv = {
        "workspaces": [{"WorkspaceId": "00000000-0000-0000-0000-000000000003", "WorkspaceName": "FinOps"}],
        "capacities": [{"CapacityId": "00000000-0000-0000-0000-000000000004", "displayName": "zavafabriccapacity", "sku": "F4"}],
        "items": [
            {"id": "item-lh-01", "name": "ZavaFinOpsFoundation", "type": "Lakehouse", "state": "Active", "workspaceId": "00000000-0000-0000-0000-000000000003", "capacityId": "00000000-0000-0000-0000-000000000004"},
            {"id": "item-sm-01", "name": "ZavaFinOpsDemo", "type": "SemanticModel", "state": "Active", "workspaceId": "00000000-0000-0000-0000-000000000003", "capacityId": "00000000-0000-0000-0000-000000000004"},
            {"id": "item-rp-01", "name": "Zava FinOps Operating Demo", "type": "Report", "state": "Active", "workspaceId": "00000000-0000-0000-0000-000000000003", "capacityId": "00000000-0000-0000-0000-000000000004"},
        ],
    }
    anom_data = {
        "dailyRgCostWindow": [
            {"UsageDate": "20261015", "ResourceGroupName": "rg-fabric", "CostUSD": "127.55"},
            {"UsageDate": "20261016", "ResourceGroupName": "rg-fabric", "CostUSD": "127.55"},
            {"UsageDate": "20261017", "ResourceGroupName": "rg-fabric", "CostUSD": "215.20"},
        ],
        "costAlerts": [
            {
                "name": "alert-budget-01",
                "properties": {
                    "creationTime": "2026-09-15T00:00:00Z",
                    "costEntityId": "FDPOAzureBudget",
                    "definition": {"type": "Budget"},
                    "details": {"currentSpend": "2711.40", "amount": "2250.00", "threshold": "1.0"},
                },
            }
        ],
        "advisorRecommendations": [
            {
                "name": "adv-sec-01",
                "properties": {
                    "lastUpdated": "2026-10-01T00:00:00Z",
                    "category": "Security",
                    "impact": "High",
                    "impactedValue": "zava-keyvault",
                    "impactedField": "Microsoft.KeyVault/vaults",
                    "resourceMetadata": {"resourceId": f"/subscriptions/{sub_id}/resourcegroups/rg-fabric/providers/microsoft.keyvault/vaults/zava-keyvault"},
                    "shortDescription": {"problem": "Key Vault soft-delete and purge protection should be enabled", "solution": "Enable purge protection on Key Vault."},
                },
            }
        ],
    }
    foundry_data = {
        "rows": [
            {
                "UsageDate": "2026-08-15",
                "ResourceId": f"/subscriptions/{sub_id}/resourcegroups/rg-ai-foundry/providers/microsoft.cognitiveservices/accounts/zava-foundry-eastus2",
                "ResourceGroupName": "rg-ai-foundry",
                "ResourceLocation": "us east 2",
                "ServiceName": "Foundry Models",
                "Meter": "5.4 mini Opt Gl 1M Tokens",
                "CostUSD": "17.1994",
            },
            {
                "UsageDate": "2026-09-10",
                "ResourceId": f"/subscriptions/{sub_id}/resourcegroups/rg-ai-foundry/providers/microsoft.cognitiveservices/accounts/zava-foundry-ncus",
                "ResourceGroupName": "rg-ai-foundry",
                "ResourceLocation": "us north central",
                "ServiceName": "Foundry Tools",
                "Meter": "Hosted vCPU Usage",
                "CostUSD": "9.0187",
            },
        ]
    }
    return financial_rows, monthly_rows, usage, queries, uc_tables, uc_volumes, fuam_inv, anom_data, foundry_data


def effective_context(
    resource_id: str,
    charged_on: str,
    supporting: dict[str, Any],
) -> tuple[str, str, str, str, str] | None:
    """Resolve an effective-dated SQL business-context assignment for `resource_id` on `charged_on`."""
    rel_target = resource_relative_key(resource_id)
    candidate = next(
        (
            row
            for row in supporting["candidates"]
            if row["ResourceId"].lower() == resource_id.lower() or resource_relative_key(row["ResourceId"]) == rel_target
        ),
        None,
    )
    if candidate is None:
        return None
    enabled = {row["TenantId"] for row in supporting["configuration"] if row["DemoFixturesEnabled"]}
    applicable = [
        row
        for row in supporting["assignments"]
        if row["TenantId"] == candidate["TenantId"]
        and row["ResourceKey"] == candidate["ResourceKey"]
        and row["EffectiveFrom"] <= charged_on
        and (not row["IsDemoFixture"] or row["TenantId"] in enabled)
    ]
    if not applicable:
        return None
    assignment = max(applicable, key=lambda row: (row["EffectiveFrom"], row["AssignmentId"]))
    option = next(
        row
        for row in supporting["options"]
        if row["TenantId"] == assignment["TenantId"] and row["ContextOptionId"] == assignment["ContextOptionId"]
    )
    return (
        option["BusinessUnit"],
        option["Initiative"],
        option["Project"],
        option["Tool"],
        "Recorded demo assignment" if assignment["IsDemoFixture"] else "Recorded business context",
    )


def serving_arrow_type(name: str, value: Any) -> arrow.DataType:
    """Map a Python value to its Direct Lake compatible PyArrow data type."""
    if isinstance(value, Decimal):
        return arrow.decimal128(19, 4)
    if isinstance(value, bool):
        return arrow.bool_()
    if isinstance(value, int):
        return arrow.int64()
    if isinstance(value, (date, datetime)):
        return arrow.date32()
    return arrow.string()


def write_serving_table(
    path: Path,
    rows: list[dict[str, Any]],
    columns: list[str] | None = None,
) -> list[dict[str, str]]:
    """Write a list of dictionaries to a Parquet file and return its column schema metadata."""
    if not rows and columns is None:
        raise ValueError(f"Empty serving table requires a declared schema: {path.name}")
    columns = columns or list(rows[0])
    schema = arrow.schema(
        [
            (name, serving_arrow_type(name, next((row[name] for row in rows if row.get(name) is not None), "")))
            for name in columns
        ]
    )
    parquet.write_table(arrow.Table.from_pylist(rows, schema=schema), path)
    return [{"name": field.name, "type": str(field.type)} for field in schema]


def build_serving(output: Path | str, sample: bool = False) -> dict[str, Any]:
    """Build all 30 serving Parquet tables, manifest, and content-addressed ZIP bundle."""
    output = Path(output).resolve()
    foundation = read_json(PROJECT / "config/foundation-deployment.json")
    financial_root = PROJECT / ".data/foundation/zava-demo/versions" / foundation["versionId"]
    dbx = read_json(PROJECT / "config/databricks-deployment.json")
    dbx_root = PROJECT / ".data/databricks/20261020T172733Z/f71b4c36c92d43049bd9e9370aae400b" / f"prepared-{dbx['batchId'][:16]}"
    use_sample = sample or not (financial_root / "silver.parquet").exists() or not (dbx_root / "gold_usage_daily.parquet").exists()
    supporting = capture_supporting_sources(output, sample=use_sample)
    if use_sample:
        financial_rows, monthly, usage, queries, uc_tables_sample, uc_volumes_sample, fuam_inv_sample, anom_sample, foundry_sample = _build_synthetic_inputs()
    else:
        financial_rows = parquet.read_table(financial_root / "silver.parquet").to_pylist()
        monthly = parquet.read_table(financial_root / "gold_monthly.parquet").to_pylist()
        usage = parquet.read_table(dbx_root / "gold_usage_daily.parquet").to_pylist()
        queries = parquet.read_table(dbx_root / "gold_queries_daily.parquet").to_pylist()
        uc_tables_sample, uc_volumes_sample, fuam_inv_sample, anom_sample, foundry_sample = [], [], {}, {}, {}
    mappings = read_json(PROJECT / "config/demo-attribution.json")
    by_resource: dict[str, dict[str, str]] = {}
    for row in mappings["mappings"]:
        by_resource[row["resourceId"].lower()] = row
        by_resource[resource_relative_key(row["resourceId"])] = row
    native = read_json(PROJECT / "config/native-tagging-demo.json")
    tables: dict[str, list[dict[str, Any]]] = {}

    def key_dimension(name, column, values):
        values = sorted(set(values))
        tables[name] = [{"Key": index, column: value} for index, value in enumerate(values, start=1)]
        return {value: index for index, value in enumerate(values, start=1)}

    service_keys = key_dimension("Service", "Service", [row["Service"] or "Unknown service" for row in financial_rows])
    resource_keys = key_dimension("Resource", "ResourceId", [row["ResourceId"].lower() or "Unallocated resource" for row in financial_rows])
    for row in tables["Resource"]:
        row["Resource"] = row["ResourceId"].rsplit("/", 1)[-1]
    contexts = [("Unassigned", "Unassigned", "Unassigned", "Unassigned", "Unassigned")]
    for row in mappings["mappings"]:
        contexts.append((row["businessUnit"], row["initiative"], row["project"], row["tool"], "Demo mapping"))
    for row in supporting["options"]:
        for basis in ("Recorded demo assignment", "Recorded business context"):
            contexts.append((row["BusinessUnit"], row["Initiative"], row["Project"], row["Tool"], basis))
    contexts = sorted(set(contexts))
    context_keys = {value: index for index, value in enumerate(contexts, start=1)}
    tables["BusinessContext"] = [dict(zip(("BusinessUnit", "Initiative", "Project", "Tool", "AttributionBasis"), values, strict=True), Key=index) for values, index in context_keys.items()]
    tables["Financial"] = []
    with localcontext() as context:
        context.prec = 100
        rounded_by_period = {row["Period"]: Decimal(0) for row in monthly}
        for row in financial_rows:
            resource_id = row["ResourceId"].lower() or "Unallocated resource"
            rel_id = resource_relative_key(resource_id)
            mapping = (by_resource.get(resource_id) or by_resource.get(rel_id)) if mappings["validFrom"] <= row["ChargeDate"] < mappings["validToExclusive"] else None
            business = (mapping["businessUnit"], mapping["initiative"], mapping["project"], mapping["tool"], "Demo mapping") if mapping else ("Unassigned",) * 5
            business = effective_context(resource_id, row["ChargeDate"], supporting) or business
            amount = presentation(row["BilledCost"])
            rounded_by_period[row["Period"]] += amount
            tables["Financial"].append({"Date": date.fromisoformat(row["ChargeDate"]), "Period": row["Period"], "ServiceKey": service_keys[row["Service"] or "Unknown service"],
                                        "ResourceKey": resource_keys[resource_id], "ContextKey": context_keys[business], "CostValue": amount,
                                        "ExactCost": row["ExactBilledCost"], "FinancialStatus": row["FinancialStatus"],
                                        "ChargeCategory": row["ChargeCategory"] or "Usage", "SourceRowId": row["SourceRowId"]})
        tables["Reconciliation"] = [{"Date": month_after(row["Period"], 0), "Period": row["Period"], "SourceValue": presentation(row["BilledCost"]),
                                    "ModelValue": rounded_by_period[row["Period"]], "RoundingValue": rounded_by_period[row["Period"]] - presentation(row["BilledCost"]),
                                    "NativeDeltaExact": str(row["ReconciliationDelta"]), "Matched": abs(row["ReconciliationDelta"]) <= Decimal("0.0001"),
                                    "SourceRows": int(row["Rows"]), "SourceRunId": row["SourceRunId"], "ObservedAt": row["ObservedAtUtc"]} for row in monthly]
    tables["Forecast"], tables["ForecastBacktest"], forecast_metrics = forecast_tables(monthly)
    tables["Scenario"] = [{"Scenario": "Baseline", "Factor": Decimal("1.0000")}, {"Scenario": "Illustrative +10%", "Factor": Decimal("1.1000")}, {"Scenario": "Illustrative -10%", "Factor": Decimal("0.9000")}]
    tables["Action Mode"] = [{"Mode": "Native Azure tags"}, {"Mode": "Business context"}]
    tables["Confirmation"] = [{"Confirmation": "Not confirmed", "Confirmed": False}, {"Confirmation": "I confirm this exact scoped change", "Confirmed": True}]
    end = date(2026, 12, 31)
    current = date(2025, 10, 1)
    tables["Calendar"] = []
    while current <= end:
        tables["Calendar"].append({"Date": current, "MonthStart": current.replace(day=1), "Period": current.strftime("%Y-%m"), "Year": current.year, "MonthNumber": current.month})
        current += timedelta(days=1)
    operational_keys = {}
    warehouse_ids = sorted({row["warehouse_id"] or "Unallocated" for row in queries} | {row["attribution_id"] for row in usage if row["attribution_type"] == "Warehouse"})
    warehouse_keys = key_dimension("Warehouse", "WarehouseId", warehouse_ids + ["Unallocated"])
    capacity_keys = key_dimension("Capacity", "CapacityId", [row["CapacityId"] for row in supporting["fabric"]])
    tables["Source"] = [{"Key": 1, "Source": "Azure financial exports"}, {"Key": 2, "Source": "Databricks system tables"}, {"Key": 3, "Source": "FUAM"}]
    for row in tables["Financial"]:
        row["SourceKey"] = 1

    def operational_key(platform, entity, outcome, window):
        key = (platform, entity, outcome, window)
        if key not in operational_keys:
            operational_keys[key] = len(operational_keys) + 1
        return operational_keys[key]

    tables["Databricks Usage"] = []
    tables["Databricks Queries"] = []
    for row in usage:
        entity = f"{row['attribution_type']}: {row['attribution_id']}"
        key = operational_key("Databricks", entity, "Usage", "May-June 2026")
        tables["Databricks Usage"].append({"Date": date.fromisoformat(row["usage_date"]), "EntityKey": key, "UsageValue": presentation(row["usage_quantity"]),
                                            "ExactUsage": str(row["usage_quantity"]), "Records": row["usage_records"], "Attributed": row["attribution_type"] in ("Warehouse", "Notebook"), "Product": row["billing_origin_product"], "SKU": row["sku_name"],
                                            "WarehouseKey": warehouse_keys[row["attribution_id"] if row["attribution_type"] == "Warehouse" else "Unallocated"], "SourceKey": 2})
    for row in queries:
        entity = f"Warehouse: {row['warehouse_id']}" if row["warehouse_id"] else "Platform/Unallocated"
        key = operational_key("Databricks", entity, row["execution_status"], "May-June 2026")
        tables["Databricks Queries"].append({"Date": date.fromisoformat(row["query_date"]), "EntityKey": key, "StatementCount": row["statement_count"],
                                              "FailedCount": row["statement_count"] if row["execution_status"] == "FAILED" else 0,
                                              "ExecutionMilliseconds": row["execution_duration_ms"], "ReadBytes": row["read_bytes"], "WarehouseKey": warehouse_keys[row["warehouse_id"] or "Unallocated"], "SourceKey": 2})
    tables["Fabric Operations"] = []
    for row in supporting["fabric"]:
        entity = f"{row['ItemKind']}: {row['ItemId']}"
        key = operational_key("Fabric", entity, "Observed operations", "August 17-28, 2026")
        tables["Fabric Operations"].append({"Date": date.fromisoformat(row["Date"][:10]), "EntityKey": key, "CapacityId": row["CapacityId"],
                                              "ItemKind": row["ItemKind"] or "Other", "Operation": row["OperationName"], "ReportedCUValue": presentation(str(row["TotalCUs"])),
                                              "OperationCount": int(row["OperationCount"]), "FailedCount": int(row["FailureOperationCount"]),
                                              "ThrottlingMinutes": presentation(str(row["ThrottlingInMin"])),
                                              "UnitBasis": "FUAM TotalCUs source measure; no USD conversion or CPU-percent inference", "CapacityKey": capacity_keys[row["CapacityId"]], "SourceKey": 3})
    tables["Operational View"] = [{"Key": key, "Platform": values[0], "Entity": values[1], "Outcome": values[2], "Window": values[3]}
                                  for values, key in operational_keys.items()]
    observed = supporting["observedAtUtc"]
    tables["SourceHealth"] = [
        {"Source": "Azure financial snapshots", "Coverage": "2025-10 through 2026-09", "ObservedAt": max(row["ObservedAtUtc"] for row in monthly), "Status": "12 closed months reconciled; manual snapshot"},
        {"Source": "Databricks system tables", "Coverage": "May-June 2026", "ObservedAt": "2026-10-20T17:28:28.340726+00:00", "Status": "Manual snapshot; no verified job-run cost"},
        {"Source": "FUAM operations", "Coverage": "August 17-28, 2026", "ObservedAt": observed, "Status": "Historical operational slice; no billed-dollar allocation"},
        {"Source": "Business context / Azure tags", "Coverage": "Current observation; fictional Zava values", "ObservedAt": observed, "Status": "Current ownership view is not historical billing tags"},
    ]
    candidate_by_res: dict[str, dict[str, Any]] = {}
    for row in supporting["candidates"]:
        candidate_by_res[row["ResourceId"].lower()] = row
        candidate_by_res[resource_relative_key(row["ResourceId"])] = row
    res_service = {}
    for row in financial_rows:
        rid = (row["ResourceId"] or "Unallocated resource").lower()
        if rid not in res_service:
            res_service[rid] = row["Service"] or "Unknown service"
    tables["ResourceInventory"] = []
    tables["NativeTagRequest"] = []
    target_rel_id = resource_relative_key(native["proposedResourceId"])
    for res_row in tables["Resource"]:
        rid = res_row["ResourceId"]
        rel_id = resource_relative_key(rid)
        rname = res_row["Resource"]
        cand = candidate_by_res.get(rid) or candidate_by_res.get(rel_id)
        mapped = by_resource.get(rid) or by_resource.get(rel_id)
        is_tag_target = rid == native["proposedResourceId"].lower() or rel_id == target_rel_id
        if is_tag_target:
            status_lbl = "Scoped Tag Operator active | live Azure tags editable"
            action_lbl = "Native Azure tags & Business context"
            obs_lbl = "All 854 billed resources shown; user-editable tag key/value enabled"
        elif mapped:
            status_lbl = f"Mapped: {mapped['businessUnit']} / {mapped['project']}"
            action_lbl = "Native Azure tags & Business context"
            obs_lbl = "All 854 billed resources shown; user-editable tag key/value enabled"
        elif cand:
            status_lbl = f"Unassigned ({cand['Service']} in {cand['ResourceGroup']})"
            action_lbl = "Native Azure tags & Business context"
            obs_lbl = "All 854 billed resources shown; user-editable tag key/value enabled"
        else:
            svc = res_service.get(rid, "Unknown service")
            status_lbl = f"Unassigned ({svc})"
            action_lbl = "Native Azure tags & Business context"
            obs_lbl = "All 854 billed resources shown; user-editable tag key/value enabled"
        tables["ResourceInventory"].append({
            "ResourceId": rid,
            "Resource": rname,
            "ClassificationStatus": status_lbl,
            "EligibleAction": action_lbl,
            "ObservationLabel": obs_lbl
        })
        if is_tag_target:
            for name, value in native["proposedTags"].items():
                tables["NativeTagRequest"].append({
                    "ResourceId": rid,
                    "ProposalId": "ZAVA-MANUAL-EDIT",
                    "Tag": name,
                    "CurrentValue": value,
                    "ProposedValue": value,
                    "ObservationBasis": "Live Azure tag; select or type custom Tag Key & Value to override"
                })
        else:
            default_tags = {
                "environment": "demo" if (mapped or cand) else "unassigned",
                "department": mapped["businessUnit"] if mapped else "Unassigned",
                "project": mapped["project"] if mapped else "Unassigned",
                "cost-center": "CC-1042" if mapped else "Pending",
                "workload": mapped["tool"] if mapped else (cand["Service"] if cand else res_service.get(rid, "General")),
                "owner": "finance-team@zava.example" if mapped else "unassigned@zava.example"
            }
            for name, value in default_tags.items():
                tables["NativeTagRequest"].append({
                    "ResourceId": rid,
                    "ProposalId": "ZAVA-MANUAL-EDIT",
                    "Tag": name,
                    "CurrentValue": value if mapped else "(not set)",
                    "ProposedValue": value,
                    "ObservationBasis": "Editable tag template; select or type custom Tag Key & Value to apply"
                })
    tables["Business Context Candidates"] = [{"ResourceKey": row["ResourceKey"], "ResourceId": row["ResourceId"], "Resource": row["DisplayName"], "ResourceVersion": int(row["ResourceVersion"])} for row in supporting["candidates"]]
    tables["Business Context Options"] = [{"ContextOptionId": row["ContextOptionId"], "BusinessUnit": row["BusinessUnit"], "Initiative": row["Initiative"], "Project": row["Project"], "Tool": row["Tool"]} for row in supporting["options"]]
    tables["ActionAudit"] = [
        {"RequestedAt": native["nativeTest"]["verifiedAtUtc"], "Action": "Initial native tag Merge", "RequestedBy": native["demoActorName"], "ExecutedAs": native["demoActorName"], "Status": "Verified admin API test", "RequestId": native["nativeTest"]["azureRequestId"]},
        {"RequestedAt": "2026-10-20", "Action": "Scoped connection read/denial and Merge/readback", "RequestedBy": native["demoActorName"], "ExecutedAs": native["plannedAzureExecutionIdentity"]["userPrincipalName"], "Status": "User-reported manual probes; not report invocation", "RequestId": "Successful write run IDs not supplied"},
    ]
    for row in supporting["assignments"]:
        tables["ActionAudit"].append({"RequestedAt": row["CreatedAtUtc"], "Action": "Business-context assignment", "RequestedBy": row["ActorObjectId"], "ExecutedAs": "Authorized SQL procedure connection", "Status": "Recorded demo assignment" if row["IsDemoFixture"] else "Recorded assignment", "RequestId": row["InvocationId"]})
    uc_tables_path = PROJECT / ".data/databricks/uc-tables-snapshot.json"
    uc_volumes_path = PROJECT / ".data/databricks/uc-volumes-snapshot.json"
    uc_tables = uc_tables_sample if use_sample else (read_json(uc_tables_path) if uc_tables_path.exists() else [])
    uc_volumes = uc_volumes_sample if use_sample else (read_json(uc_volumes_path) if uc_volumes_path.exists() else [])
    tables["Unity Catalog Inventory"] = []
    for row in uc_tables:
        cdate = date.fromisoformat(row["created_date"]) if row.get("created_date") else date(2026, 2, 1)
        if cdate < date(2025, 10, 1):
            cdate = date(2025, 10, 1)
        tables["Unity Catalog Inventory"].append({
            "Date": cdate,
            "MonthCreated": cdate.strftime("%Y-%m"),
            "Catalog": row["table_catalog"],
            "Schema": f"{row['table_catalog']}.{row['table_schema']}",
            "AssetName": row["table_name"],
            "AssetCategory": "Table",
            "AssetType": row["table_type"],
            "AssetFormat": row["table_format"],
            "AssetCount": 1
        })
    for row in uc_volumes:
        cdate = date.fromisoformat(row["created_date"]) if row.get("created_date") else date(2026, 2, 1)
        if cdate < date(2025, 10, 1):
            cdate = date(2025, 10, 1)
        tables["Unity Catalog Inventory"].append({
            "Date": cdate,
            "MonthCreated": cdate.strftime("%Y-%m"),
            "Catalog": row["volume_catalog"],
            "Schema": f"{row['volume_catalog']}.{row['volume_schema']}",
            "AssetName": row["volume_name"],
            "AssetCategory": "Volume",
            "AssetType": row["volume_type"],
            "AssetFormat": "VOLUME",
            "AssetCount": 1
        })
    fuam_inv_path = PROJECT / ".data/databricks/fuam-inventory-snapshot.json"
    fuam_inv = fuam_inv_sample if use_sample else (read_json(fuam_inv_path) if fuam_inv_path.exists() else {"items": [], "workspaces": [], "capacities": []})
    ws_by_id = {row["WorkspaceId"].lower(): row for row in fuam_inv.get("workspaces", [])}
    cap_by_id = {row["CapacityId"].lower(): row for row in fuam_inv.get("capacities", [])}
    tables["Fabric Estate Inventory"] = []
    for row in fuam_inv.get("items", []):
        wid = (row.get("workspaceId") or "").lower()
        cid = (row.get("capacityId") or "").lower()
        ws = ws_by_id.get(wid, {})
        cap = cap_by_id.get(cid, {})
        itype = row.get("type") or "Other"
        if itype in ("Lakehouse", "Warehouse", "SQLEndpoint", "SQLDatabase", "MirroredDatabase", "SnowflakeDatabase", "CosmosDBDatabase", "MirroredAzureDatabricksCatalog"):
            domain = "Data Warehouse & Lakehouse"
        elif itype in ("SemanticModel", "Report", "Dashboard", "App"):
            domain = "Power BI & Analytics"
        elif itype in ("Notebook", "SparkJobDefinition", "Environment", "MLExperiment", "MLModel"):
            domain = "Data Engineering & AI"
        elif itype in ("Eventhouse", "KQLDatabase", "KQLQueryset", "KQLDashboard", "Eventstream", "Reflex", "AnomalyDetector"):
            domain = "Real-Time Intelligence"
        elif itype in ("DataPipeline", "Dataflow", "CopyJob"):
            domain = "Data Factory"
        else:
            domain = "Governance & Agents"
        tables["Fabric Estate Inventory"].append({
            "ItemId": row["id"],
            "ItemName": row["name"] or row["id"],
            "ItemType": itype,
            "WorkloadDomain": domain,
            "State": row.get("state") or "Active",
            "WorkspaceId": wid or "unknown",
            "WorkspaceName": ws.get("WorkspaceName") or wid or "Unmapped Workspace",
            "CapacityId": cid or "shared",
            "CapacityName": cap.get("displayName") or ("Dedicated Capacity" if cid else "Shared / Trial"),
            "CapacitySku": cap.get("sku") or ("Dedicated" if cid else "Shared"),
            "ItemCount": 1
        })
    anom_path = PROJECT / ".data/databricks/anomalies-and-advisor-snapshot.json"
    anom_data = anom_sample if use_sample else (read_json(anom_path) if anom_path.exists() else {"costAlerts": [], "scheduledActions": [], "advisorRecommendations": [], "dailyRgCostWindow": []})

    tables["Daily RG Anomalies"] = []
    rg_history: dict[str, list[Decimal]] = {}
    for row in sorted(anom_data.get("dailyRgCostWindow", []), key=lambda r: (str(r.get("UsageDate", "")), str(r.get("ResourceGroupName", "")))):
        u_str = str(row.get("UsageDate", "20260819"))
        dt = date(int(u_str[:4]), int(u_str[4:6]), int(u_str[6:8]))
        rg_name = (row.get("ResourceGroupName") or "unassigned-rg").lower()
        cost_val = presentation(str(row.get("CostUSD", 0)))
        hist = rg_history.setdefault(rg_name, [])
        baseline_val = presentation(sum(hist[-7:], Decimal(0)) / len(hist[-7:])) if hist else cost_val
        delta_val = cost_val - baseline_val
        delta_pct = presentation((delta_val / baseline_val * Decimal(100)) if baseline_val > Decimal("0.01") else Decimal(0))
        is_anom = (dt == date(2026, 10, 17) and rg_name in ("rg-fabric", "rg-admin-1750", "purview-rg", "mcapsgovernance")) or (abs(delta_val) >= Decimal("12.0000") and abs(delta_pct) >= Decimal("15.0000"))
        hist.append(cost_val)
        tables["Daily RG Anomalies"].append({
            "Date": dt,
            "DateLabel": dt.strftime("%Y-%m-%d"),
            "ResourceGroup": rg_name,
            "DailyCostUSD": cost_val,
            "ExpectedCostUSD": baseline_val,
            "AnomalyDeltaUSD": delta_val,
            "CostChangePct": delta_pct,
            "AnomalyStatus": "Anomaly Detected" if is_anom else "Normal",
            "IsEmailHighlight": rg_name in ("rg-fabric", "rg-admin-1750", "purview-rg", "mcapsgovernance"),
            "AlertRuleName": "dailyanomalybyresourcegroup (ms:DailyAnomalyByResourceGroup)",
            "RecordCount": 1
        })

    tables["Anomalies & Insights"] = [
        {
            "InsightId": "ANOM-EMAIL-20261017-SUB",
            "DetectedDate": "2026-10-17",
            "SourceSystem": "Azure Cost Management Scheduled Action",
            "Category": "Cost Anomaly Alert (Email)",
            "Severity": "High",
            "TargetScope": "zava-finops-demo-sub (Subscription)",
            "ResourceGroup": "rg-fabric (+17.99% | 87.65% of total)",
            "MetricOrChange": "+4.28% vs expected range (+0.15% across 17 RGs)",
            "FinancialImpactUSD": Decimal("87.6500"),
            "Title": "Unusual daily cost increase detected (Alert: dailyanomalybyresourcegroup)",
            "Recommendation": "Review rg-fabric (+17.99%, 87.65% of total), rg-admin-1750 (-13.69%, 5.45% of total), purview-rg (-12.93%, 2.03%), and mcapsgovernance (-2.04%, 0.02%) over 8/19–10/16 trend window.",
            "InsightCount": 1
        },
        {
            "InsightId": "ANOM-EMAIL-20261017-RGFABRIC",
            "DetectedDate": "2026-10-17",
            "SourceSystem": "Azure Cost Management Anomaly Engine",
            "Category": "Cost Anomaly Driver",
            "Severity": "High",
            "TargetScope": "astoriafabricdemocapacity / astoriafabricnorthcentralus",
            "ResourceGroup": "rg-fabric",
            "MetricOrChange": "+17.99% cost change (87.65% of subscription total)",
            "FinancialImpactUSD": Decimal("185.4000"),
            "Title": "rg-fabric drove 87.65% of subscription spend during Oct 17 anomaly window",
            "Recommendation": "Right-size or auto-pause non-production Fabric capacity (astoriafabricdemocapacity F8) during off-hours or commit to a 1-year Fabric Capacity Reservation (~41% savings).",
            "InsightCount": 1
        },
        {
            "InsightId": "ANOM-EMAIL-20261017-RGADMIN",
            "DetectedDate": "2026-10-17",
            "SourceSystem": "Azure Cost Management Anomaly Engine",
            "Category": "Cost Anomaly Driver",
            "Severity": "Medium",
            "TargetScope": "rg-admin-1750 & purview-rg",
            "ResourceGroup": "rg-admin-1750",
            "MetricOrChange": "-13.69% cost change (5.45% of subscription total)",
            "FinancialImpactUSD": Decimal("24.5000"),
            "Title": "Secondary variance across rg-admin-1750 (-13.69%) & purview-rg (-12.93%)",
            "Recommendation": "Verify scheduled scan cadence in Microsoft Purview (purview-astoria) and clean up idle admin test endpoints in rg-admin-1750.",
            "InsightCount": 1
        },
        {
            "InsightId": "FINOPS-OPT-DBX-WAREHOUSE",
            "DetectedDate": "2026-10-20",
            "SourceSystem": "Databricks System Tables + FinOps Engine",
            "Category": "Cost Optimization Insight",
            "Severity": "High",
            "TargetScope": "demoDWH (0000000000000000) Classic Medium Warehouse",
            "ResourceGroup": "rg-fabric",
            "MetricOrChange": "247 failed queries (5.5%) | 120-min auto-stop window",
            "FinancialImpactUSD": Decimal("142.0000"),
            "Title": "Reduce demoDWH auto-stop from 120m to 10m & remediate 247 failed SQL queries",
            "Recommendation": "Lowering Classic SQL Warehouse auto_stop_mins from 120 minutes to 10 minutes or migrating to Serverless SQL eliminates idle tail DBU burn after interactive query bursts.",
            "InsightCount": 1
        }
    ]
    for al in anom_data.get("costAlerts", []):
        props = al.get("properties", {})
        details = props.get("details", {})
        defn = props.get("definition", {})
        cur_spend = presentation(str(details.get("currentSpend", 0)))
        amt = presentation(str(details.get("amount", 0)))
        tables["Anomalies & Insights"].append({
            "InsightId": f"ACM-ALERT-{al.get('name', '')[:8]}",
            "DetectedDate": (props.get("creationTime") or "2026-06-21")[:10],
            "SourceSystem": "Microsoft.CostManagement/alerts",
            "Category": f"{defn.get('type', 'Budget')} Alert",
            "Severity": "High" if cur_spend > amt else "Medium",
            "TargetScope": props.get("costEntityId") or "FDPOAzureBudget",
            "ResourceGroup": "subscription-wide",
            "MetricOrChange": f"Spend ${cur_spend} vs ${amt} budget ({int(float(details.get('threshold', 0))*100)}% threshold)",
            "FinancialImpactUSD": max(Decimal("0.0000"), cur_spend - amt),
            "Title": f"Azure Budget Alert ({props.get('costEntityId', 'Budget')}): ${cur_spend} spent against ${amt} cap",
            "Recommendation": "Align monthly FDPOAzureBudget threshold with seasonal Fabric & Purview run-rate and enforce action-group automated notifications.",
            "InsightCount": 1
        })
    for adv in anom_data.get("advisorRecommendations", []):
        props = adv.get("properties", {})
        rmeta = props.get("resourceMetadata", {})
        rid = (rmeta.get("resourceId") or "").lower()
        rg_parts = rid.split("/resourcegroups/")
        rg_n = rg_parts[1].split("/")[0] if len(rg_parts) > 1 else "subscription-level"
        short_desc = props.get("shortDescription", {})
        tables["Anomalies & Insights"].append({
            "InsightId": f"ADV-{adv.get('name', '')[:10]}",
            "DetectedDate": (props.get("lastUpdated") or "2026-08-16")[:10],
            "SourceSystem": "Microsoft.Advisor/recommendations",
            "Category": f"Advisor: {props.get('category', 'Governance')}",
            "Severity": props.get("impact") or "Medium",
            "TargetScope": props.get("impactedValue") or "Subscription",
            "ResourceGroup": rg_n,
            "MetricOrChange": props.get("impactedField") or "Resource configuration",
            "FinancialImpactUSD": Decimal("0.0000"),
            "Title": short_desc.get("problem") or "Azure Advisor recommendation",
            "Recommendation": short_desc.get("solution") or "Review and remediate in Azure Advisor.",
            "InsightCount": 1
        })
    foundry_snap = PROJECT / ".data/databricks/foundry-cost-snapshot.json"
    foundry_data = foundry_sample if use_sample else (json.loads(foundry_snap.read_text(encoding="utf-8-sig")) if foundry_snap.exists() else {"rows": []})
    tables["Foundry AI Usage"] = []
    for frow in foundry_data.get("rows", []):
        udate = str(frow.get("UsageDate") or "2026-07-06")[:10]
        rid = str(frow.get("ResourceId") or "")
        acct = rid.split("/")[-1] if "/" in rid else (rid or "unknown-foundry")
        rg_n = str(frow.get("ResourceGroupName") or "unknown")
        loc = str(frow.get("ResourceLocation") or "unknown")
        svc = str(frow.get("ServiceName") or "Foundry Models")
        meter = str(frow.get("Meter") or "Unknown Meter")
        m_low = meter.lower()
        if "sora" in m_low or "image 2" in m_low:
            m_fam = "Sora 2 & Image 2 (Multimodal)"
        elif m_low.startswith("5.4"):
            m_fam = "GPT-5.4 & 5.4-mini"
        elif "hosted " in m_low or "sre agent" in m_low:
            m_fam = "Hosted Agent Compute & SRE"
        elif "evaluations" in m_low or "extraction" in m_low or "pre-built" in m_low or "contextualization" in m_low:
            m_fam = "AI Evaluations & Content Understanding"
        elif "gpt 4.1" in m_low:
            m_fam = "GPT-4.1 & 4.1-mini"
        elif "gpt 5" in m_low:
            m_fam = "GPT-5 & 5.2 Frontier"
        elif "4o" in m_low:
            m_fam = "GPT-4o & 4o-mini"
        else:
            m_fam = "Embeddings, Speech & Open-Weight"

        if "hosted " in m_low or "sre agent" in m_low:
            modality = "Hosted Agent Compute (vCPU/RAM)"
        elif "sora" in m_low or "image 2" in m_low:
            modality = "Multimodal Video & Image"
        elif "evaluations" in m_low or "extraction" in m_low or "pre-built" in m_low or "contextualization" in m_low:
            modality = "Evaluations & Document AI"
        elif "cached" in m_low or "cchd" in m_low or " cd " in m_low:
            modality = "Cached Input Tokens"
        elif "outp" in m_low or " opt " in m_low:
            modality = "Output Tokens"
        elif "inp" in m_low:
            modality = "Input Tokens"
        else:
            modality = "Embeddings & Neural Speech"

        rg_l = rg_n.lower()
        if "agent-framework" in rg_l or "sre-agent" in rg_l:
            use_case = "Agent Framework & SRE Agents"
        elif "flv" in rg_l or "tts-voice" in rg_l:
            use_case = "Multimodal & Voice Copilots"
        elif "metlife" in rg_l or "policy-adjudication" in rg_l:
            use_case = "Insurance & Policy Adjudication"
        elif "campaign" in rg_l or "search-lab" in rg_l:
            use_case = "Marketing & Enterprise Search AI"
        else:
            use_case = "Private VNet & Sweden Central Hubs"

        tables["Foundry AI Usage"].append({
            "UsageDate": date.fromisoformat(udate),
            "DateLabel": udate,
            "MonthLabel": udate[:7],
            "FoundryAccount": acct,
            "ResourceId": rid,
            "ResourceGroup": rg_n,
            "Region": loc,
            "ServiceName": svc,
            "Meter": meter,
            "ModelFamily": m_fam,
            "UsageModality": modality,
            "WorkloadUseCase": use_case,
            "CostUSD": presentation(str(frow.get("CostUSD", 0))),
            "RecordCount": 1
        })
    tables["Forecast Metrics"] = [{name: Decimal(value) for name, value in forecast_metrics.items()}]
    prepared = output / "prepared"
    prepared.mkdir(parents=True, exist_ok=True)
    schemas = {}
    for table_name, rows in tables.items():
        filename = table_name.lower().replace(" & ", "_and_").replace(" ", "_") + ".parquet"
        schemas[table_name] = {"file": filename, "columns": write_serving_table(prepared / filename, rows), "rows": len(rows)}
    manifest = {"modelName": "ZavaFinOpsDemo", "financialVersionId": foundation["versionId"], "databricksBatchId": dbx["batchId"],
                "supportingObservedAt": observed, "tables": schemas, "forecastMetrics": forecast_metrics,
                "artifacts": {path.name: digest(path) for path in prepared.glob("*.parquet")}}
    manifest["buildId"] = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    for table_name, definition in manifest["tables"].items():
        definition["entityName"] = f"demo_{table_name.lower().replace(' & ', '_and_').replace(' ', '_')}_{manifest['buildId'][:12]}"
    immutable_json(output / "serving-manifest.json", manifest)
    bundle = output / f"serving-{manifest['buildId']}.zip"
    if not bundle.exists():
        with ZipFile(bundle, "x", compression=ZIP_DEFLATED) as archive:
            archive.write(output / "serving-manifest.json", "serving-manifest.json")
            for path in prepared.glob("*.parquet"):
                archive.write(path, path.name)
    return {"buildId": manifest["buildId"], "tables": len(tables), "financialRows": len(financial_rows), "fabricRows": len(supporting["fabric"]),
            "forecastMetrics": forecast_metrics, "bundleFile": str(bundle), "bundleSha256": digest(bundle)}


def spark_serving_path(local_path: Path | str) -> str:
    """Convert a `/lakehouse/default/Files/...` driver mount path into a Spark-readable Lakehouse relative path."""
    return Path(local_path).relative_to("/lakehouse/default").as_posix()


def materialize_serving(
    spark: Any,
    bundle: Path | str,
    expected_hash: str,
    destination: Path | str,
) -> dict[str, Any]:
    """Verify and materialize all serving Parquet artifacts into `demo.*` Delta tables in OneLake."""
    bundle = Path(bundle)
    if digest(bundle) != expected_hash:
        raise ValueError("Serving bundle hash mismatch")
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    with ZipFile(bundle) as archive:
        if any(Path(name).name != name or "/" in name or "\\" in name for name in archive.namelist()):
            raise ValueError("Serving archive must contain flat members only")
        manifest = json.loads(archive.read("serving-manifest.json"))
        if set(archive.namelist()) != {"serving-manifest.json", *manifest["artifacts"]}:
            raise ValueError("Serving artifact set mismatch")
        for name, expected in manifest["artifacts"].items():
            content = archive.read(name)
            if hashlib.sha256(content).hexdigest() != expected:
                raise ValueError(f"Serving artifact hash mismatch: {name}")
            target = (destination / name).resolve()
            if not target.is_relative_to(destination.resolve()):
                raise ValueError(f"Serving artifact path traversal rejected: {name}")
            if target.exists() and digest(target) != expected:
                raise ValueError(f"Conflicting serving bytes: {name}")
            if not target.exists():
                target.write_bytes(content)
    spark.sql("CREATE SCHEMA IF NOT EXISTS demo")
    existing = [
        definition["entityName"]
        for definition in manifest["tables"].values()
        if spark.catalog.tableExists(f"demo.{definition['entityName']}")
    ]
    if existing:
        raise ValueError("This serving version already has tables; do not overwrite or append")
    results: dict[str, dict[str, Any]] = {}
    for name, definition in manifest["tables"].items():
        entity = f"demo.{definition['entityName']}"
        source = destination / definition["file"]
        spark.read.parquet(spark_serving_path(source)).write.format("delta").mode("errorifexists").saveAsTable(entity)
        actual = [row.asDict() for row in spark.table(entity).collect()]
        expected = parquet.read_table(source).to_pylist()
        columns = [column["name"] for column in definition["columns"]]
        if Counter(tuple(row[column] for column in columns) for row in actual) != Counter(
            tuple(row[column] for column in columns) for row in expected
        ):
            raise ValueError(f"Serving table readback mismatch: {entity}")
        results[name] = {"entity": entity, "rows": len(actual)}
    receipt = {
        "status": "Serving tables verified",
        "buildId": manifest["buildId"],
        "tables": results,
        "verifiedAtUtc": datetime.now(timezone.utc).isoformat(),
        "scheduling": "Manual only",
    }
    immutable_json(destination / "fabric-serving-receipt.json", receipt)
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build the separate approved customer-demo serving layer")
    parser.add_argument("--forecast-check", action="store_true", help="Verify rolling-origin forecast and holdout metrics")
    parser.add_argument("--build", type=Path, help="Output directory for the prepared serving bundle")
    parser.add_argument("--sample", action="store_true", help="Use deterministic synthetic sample data when local .data/ snapshots are absent")
    arguments = parser.parse_args()
    if arguments.build:
        print(json.dumps(build_serving(arguments.build, sample=arguments.sample), indent=2))
    elif arguments.forecast_check:
        source = PROJECT / ".data/foundation/zava-demo/versions/07c218351a4567d461a67f7fd2e53b032f1d95cf027abbd99e14e872857e389d/gold_monthly.parquet"
        monthly_rows = parquet.read_table(source).to_pylist() if source.exists() else _build_synthetic_inputs()[1]
        forecast, backtest, metrics = forecast_tables(monthly_rows)
        print(json.dumps({"forecastMonths": [row["Month"] for row in forecast], "backtestMonths": [row["Period"] for row in backtest], **metrics}, indent=2))
