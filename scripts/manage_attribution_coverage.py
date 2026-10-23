"""Reversible Business-Context Attribution Manager for Zava FinOps Demo.

Allows instant toggling between:
  - 'baseline': Original 5-resource, Jun-Sep mapping (68.4% Unassigned / $11,570.42 unassigned)
  - 'governed': Realistic enterprise attribution across 770 of 854 resources (96.6% attributed / 3.4% unassigned tail)
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import time
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from finops_config import (
    DEFAULT_SUBSCRIPTION_ID,
    get_azure_access_token,
    load_customer_config,
    normalize_resource_id,
    resource_relative_key,
    safe_urlopen,
    validate_build_suffix,
    validate_uuid,
)

ROOT: Path = Path(__file__).resolve().parent.parent
BASELINE_FILE: Path = ROOT / "config" / "demo-attribution.baseline.json"
GOVERNED_FILE: Path = ROOT / "config" / "demo-attribution.governed.json"
ACTIVE_FILE: Path = ROOT / "config" / "demo-attribution.json"
STATE_FILE: Path = ROOT / "config" / "attribution-mode-state.json"

# Pre-built serving suffixes in ZavaFinOpsFoundation lakehouse (schema: demo)
SERVING_SUFFIXES: dict[str, str] = {
    "baseline": "92df0dc65188",  # 5 mapped resources, 68.4% Unassigned ($11,570.42 unassigned)
    "governed": "75b4187f7d3f",  # 770 mapped resources, 3.4% Unassigned ($576.16 unassigned) + Anomalies & Foundry AI Usage
}


def classify_resource(rid: str) -> tuple[str, str, str, str] | None:
    """Deterministically assign realistic Zava Business Unit, Initiative, Project, and Tool with natural variance."""
    rid_l = rid.lower()
    if rid_l == "unallocated resource" or not rid_l.startswith("/subscriptions/"):
        return None

    rel_key = resource_relative_key(rid_l)

    # Preserve the 5 flagship resources on their exact canonical mappings
    canonical: dict[str, tuple[str, str, str, str]] = {
        "/resourcegroups/rg-fabric/providers/microsoft.fabric/capacities/astoriafabricnorthcentralus": (
            "Finance",
            "Finance Modernization",
            "Finance Analytics",
            "Fabric Analytics",
        ),
        "/resourcegroups/rg-fabric/providers/microsoft.databricks/workspaces/databricksworkspace": (
            "Finance",
            "Finance Modernization",
            "Finance Analytics",
            "Databricks Engineering",
        ),
        "/resourcegroups/purview-rg/providers/microsoft.purview/accounts/purview-astoria": (
            "Finance",
            "Finance Modernization",
            "Finance Analytics",
            "Purview Governance",
        ),
        "/resourcegroups/rg-fabric/providers/microsoft.fabric/capacities/astoriafabricdemocapacity": (
            "Customer Operations",
            "Customer Intelligence",
            "Customer Insights",
            "Fabric Analytics",
        ),
        "/resourcegroups/rg-fabric/providers/microsoft.fabric/capacities/astoriafabricoaptest": (
            "Data Platform",
            "Enterprise Data Foundation",
            "Platform Enablement",
            "Fabric Analytics",
        ),
    }
    if rel_key in canonical:
        return canonical[rel_key]

    parts = rid_l.split("/resourcegroups/")
    rg = parts[1].split("/")[0] if len(parts) > 1 else "none"
    h = int(hashlib.sha256(rid_l.encode("utf-8")).hexdigest()[:8], 16) % 100

    # Intentional ~3.4% unassigned tail (untagged VMs, alert rules, ad-hoc test RGs)
    if rg.startswith("azureapp-auto-alerts-") or rg in (
        "rg-vms",
        "default-activitylogalerts",
        "rg-bma-hol",
        "rg-privateendpointtest",
        "rg-admin-1750",
        "unknown",
        "rg-vm",
        "functionappdemo",
        "none",
    ):
        return None

    if rg == "purview-rg":
        return ("Finance", "Finance Modernization", "Enterprise Data Governance", "Purview & Analysis Services")
    if rg in ("rg-sql-vm", "rg-sql"):
        if h < 65:
            return ("Data Platform", "Enterprise Data Foundation", "SQL Mirroring & CDC", "Azure SQL & Mirroring")
        return ("Finance", "Finance Modernization", "ERP & Ledger Replication", "Azure SQL & Mirroring")
    if rg == "rg-vnet":
        return ("Cloud Infrastructure & Security", "Zero-Trust Network Hub", "Hub VNet & Firewall", "Azure Networking")
    if rg in ("rg-costmgmt-export", "mcapsgovernance"):
        return ("Finance", "Finance Modernization", "FinOps & Cost Governance", "FinOps Hub & Exports")
    if rg in ("rg-eventhubs", "rti-demo_group"):
        return ("Customer Operations", "Customer Intelligence", "Real-Time Event Streaming", "Fabric RTI & Event Hubs")
    if rg in ("rg-actuaryiq", "rg-databricks"):
        return ("Risk & Actuarial", "Actuarial Intelligence", "ActuaryIQ Pricing & Risk", "Databricks Engineering")
    if rg in ("rg-ai-foundry", "rg-foundry-template-test", "rg-bankingagenthol", "azure-ml-rg", "rg-speech-services",
              "rg-projects-dev", "rg-cosmosdb", "rg-agenthol", "rg-azureopenai"):
        return ("AI & Digital Innovation", "Enterprise AI Foundry", "Banking & Claims Copilots", "Microsoft Foundry & AI")
    if rg == "rg-fabric" or rg == "rg-storage":
        return ("Data Platform", "Enterprise Data Foundation", "OneLake & Storage Connectivity", "Fabric Analytics")

    # Distribute the 688 ephemeral Databricks cluster resources across business workloads with a small 5% unassigned tail
    if rg == "databricks-rg-databricksworkspace-kja7fr373pbfo":
        if h < 35:
            return ("Finance", "Finance Modernization", "Finance Analytics", "Databricks Engineering")
        if h < 62:
            return ("Risk & Actuarial", "Actuarial Intelligence", "ActuaryIQ Pricing & Risk", "Databricks Engineering")
        if h < 82:
            return ("Customer Operations", "Customer Intelligence", "Churn & Propensity ML", "Databricks Engineering")
        if h < 95:
            return ("Data Platform", "Enterprise Data Foundation", "Medallion Ingestion Pipelines", "Databricks Engineering")
        return None

    return ("Data Platform", "Enterprise Data Foundation", "Platform Enablement", "Shared Infrastructure")


def _load_silver_rows() -> list[dict[str, Any]]:
    """Load Silver financial rows from local `.data/` if available, otherwise return empty list."""
    foundation = json.loads((ROOT / "config/foundation-deployment.json").read_text(encoding="utf-8"))
    silver_path = ROOT / ".data/foundation/zava-demo/versions" / foundation["versionId"] / "silver.parquet"
    if silver_path.exists():
        return pq.read_table(silver_path).to_pylist()
    return []


def generate_governed_attribution() -> dict[str, Any]:
    """Generate and persist the governed business-context attribution mapping file."""
    rows = _load_silver_rows()
    if GOVERNED_FILE.exists():
        payload = json.loads(GOVERNED_FILE.read_text(encoding="utf-8"))
        ACTIVE_FILE.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return summarize_coverage(rows, payload)

    if rows:
        distinct_rids = sorted({(r["ResourceId"] or "Unallocated resource").lower() for r in rows})
    else:
        existing = json.loads(ACTIVE_FILE.read_text(encoding="utf-8")) if ACTIVE_FILE.exists() else {"mappings": []}
        distinct_rids = sorted({m["resourceId"].lower() for m in existing.get("mappings", [])})

    mappings: list[dict[str, str]] = []
    for rid in distinct_rids:
        assigned = classify_resource(rid)
        if assigned:
            bu, init, proj, tool = assigned
            mappings.append(
                {
                    "resourceId": normalize_resource_id(rid, DEFAULT_SUBSCRIPTION_ID),
                    "businessUnit": bu,
                    "initiative": init,
                    "project": proj,
                    "tool": tool,
                }
            )

    payload = {
        "description": "Reversible high-coverage Zava governance simulation (~96.6% spend attributed across 6 Business Units; ~3.4% unassigned tail retained for remediation demo). Revert anytime with: python scripts/manage_attribution_coverage.py --mode baseline",
        "mode": "governed",
        "validFrom": "2025-10-01",
        "validToExclusive": "2026-10-01",
        "mappings": mappings,
    }
    GOVERNED_FILE.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    ACTIVE_FILE.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return summarize_coverage(rows, payload)


def restore_baseline_attribution() -> dict[str, Any]:
    """Restore the baseline 5-resource attribution mapping file."""
    baseline_text = BASELINE_FILE.read_text(encoding="utf-8")
    ACTIVE_FILE.write_text(baseline_text, encoding="utf-8")
    rows = _load_silver_rows()
    return summarize_coverage(rows, json.loads(baseline_text))


def summarize_coverage(rows: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
    """Compute spend and resource attribution coverage metrics across Business Units."""
    if not rows and STATE_FILE.exists():
        cached = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        cached["mode"] = config.get("mode", "baseline")
        cached["mappedResourceCount"] = len(config.get("mappings", []))
        return cached

    by_res = {resource_relative_key(m["resourceId"]): m for m in config["mappings"]}
    v_from, v_to = config["validFrom"], config["validToExclusive"]
    spend_by_bu: Counter[str] = Counter()
    res_by_bu: Counter[str] = Counter()
    seen_res: set[str] = set()

    for r in rows:
        rid_rel = resource_relative_key(r["ResourceId"] or "Unallocated resource")
        cost = Decimal(str(r["BilledCost"]))
        m = by_res.get(rid_rel) if (v_from <= r["ChargeDate"] < v_to) else None
        bu = m["businessUnit"] if m else "Unassigned"
        spend_by_bu[bu] += cost
        if rid_rel not in seen_res:
            seen_res.add(rid_rel)
            res_by_bu[by_res[rid_rel]["businessUnit"] if rid_rel in by_res else "Unassigned"] += 1

    total = sum(spend_by_bu.values(), Decimal(0)) or Decimal("1")
    unassigned = spend_by_bu.get("Unassigned", Decimal(0))
    return {
        "mode": config.get("mode", "baseline"),
        "validFrom": v_from,
        "validToExclusive": v_to,
        "mappedResourceCount": len(config["mappings"]),
        "totalDistinctResources": len(seen_res),
        "totalBilledUSD": str(round(total, 2)),
        "attributedUSD": str(round(total - unassigned, 2)),
        "unassignedUSD": str(round(unassigned, 2)),
        "attributedSharePct": round(float((total - unassigned) / total * 100), 2),
        "unassignedSharePct": round(float(unassigned / total * 100), 2),
        "spendByBusinessUnit": {
            k: f"${round(v, 2):,.2f} ({round(float(v / total * 100), 1)}%)" for k, v in spend_by_bu.most_common()
        },
        "resourcesByBusinessUnit": dict(res_by_bu.most_common()),
    }


def switch_semantic_model_build_suffix(new_suffix: str) -> int:
    """Update Direct Lake partition `entityName` build suffixes across all TMDL tables."""
    validated_suffix = validate_build_suffix(new_suffix)
    tables_root = ROOT / "cloud-dev/ZavaFinOpsDemo.SemanticModel/definition/tables"
    anomaly_tables = {"Daily RG Anomalies.tmdl", "Anomalies & Insights.tmdl", "Foundry AI Usage.tmdl"}
    changed = 0
    for path in tables_root.glob("*.tmdl"):
        target = "75b4187f7d3f" if (path.name in anomaly_tables and validated_suffix == "92df0dc65188") else validated_suffix
        text = path.read_text(encoding="utf-8")
        updated = re.sub(r"_(92df0dc65188|7b863d35eac4|2d98f495bc56|75b4187f7d3f|[0-9a-f]{12})\b", f"_{target}", text)
        if updated != text:
            path.write_text(updated, encoding="utf-8")
            changed += 1
    return changed


def deploy_semantic_model_switch(mode: str, config_path: Path | None = None) -> dict[str, Any]:
    """Update the remote Fabric Direct Lake semantic model definition, refresh, and verify via DAX."""
    cfg = load_customer_config(config_path)
    subscription_id = cfg.get("subscriptionId", DEFAULT_SUBSCRIPTION_ID)
    workspace_id = validate_uuid(cfg["fabric"]["workspaceId"], "fabric.workspaceId")
    model_id = validate_uuid(cfg["fabric"]["semanticModelId"], "fabric.semanticModelId")

    target_suffix = SERVING_SUFFIXES[mode]
    changed = switch_semantic_model_build_suffix(target_suffix)
    fab_token = get_azure_access_token("https://api.fabric.microsoft.com", subscription_id=subscription_id)
    model_root = ROOT / "cloud-dev/ZavaFinOpsDemo.SemanticModel"
    parts: list[dict[str, str]] = []
    for f in sorted(model_root.rglob("*")):
        if f.is_file() and (f.suffix == ".tmdl" or f.name == "definition.pbism"):
            rel = f.relative_to(model_root).as_posix()
            b64 = base64.b64encode(f.read_bytes()).decode("ascii")
            parts.append({"path": rel, "payload": b64, "payloadType": "InlineBase64"})

    body = json.dumps({"definition": {"format": "TMDL", "parts": parts}}).encode("utf-8")
    _, resp_headers, _ = safe_urlopen(
        f"https://api.fabric.microsoft.com/v1/workspaces/{workspace_id}/semanticModels/{model_id}/updateDefinition",
        method="POST",
        headers={
            "Authorization": f"Bearer {fab_token}",
            "Content-Type": "application/json",
            "x-ms-fabric-skill": "semantic-model-authoring",
        },
        body=body,
        timeout=60,
    )
    op_id = resp_headers.get("x-ms-operation-id")

    if op_id:
        validate_uuid(op_id, "x-ms-operation-id")
        for _ in range(30):
            _, _, op_raw = safe_urlopen(
                f"https://api.fabric.microsoft.com/v1/operations/{op_id}",
                headers={"Authorization": f"Bearer {fab_token}", "x-ms-fabric-skill": "semantic-model-authoring"},
            )
            op_data = json.loads(op_raw.decode("utf-8"))
            if op_data.get("status") == "Succeeded":
                break
            if op_data.get("status") == "Failed":
                raise RuntimeError(f"Model update failed: {op_data}")
            time.sleep(2)

    pbi_token = get_azure_access_token("https://analysis.windows.net/powerbi/api", subscription_id=subscription_id)
    safe_urlopen(
        f"https://api.powerbi.com/v1.0/myorg/groups/{workspace_id}/datasets/{model_id}/refreshes",
        method="POST",
        headers={"Authorization": f"Bearer {pbi_token}", "Content-Type": "application/json"},
        body=b'{"notifyOption":"NoNotification"}',
    )
    time.sleep(6)

    dax_payload = json.dumps(
        {
            "queries": [
                {
                    "query": 'EVALUATE ROW("Billed_USD", [Billed USD], "Unassigned_USD", [Unassigned USD], "Unassigned_Share", [Unassigned Share], "Unexplained_USD", [Unexplained USD])'
                }
            ],
            "serializerSettings": {"includeNulls": True},
        }
    ).encode("utf-8")
    _, _, dax_raw = safe_urlopen(
        f"https://api.powerbi.com/v1.0/myorg/groups/{workspace_id}/datasets/{model_id}/executeQueries",
        method="POST",
        headers={"Authorization": f"Bearer {pbi_token}", "Content-Type": "application/json"},
        body=dax_payload,
    )
    dax_res = json.loads(dax_raw.decode("utf-8"))
    live_row = dax_res["results"][0]["tables"][0]["rows"][0]
    return {"servingSuffix": target_suffix, "tmdlTablesUpdated": changed, "liveModelMetrics": live_row}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Toggle Zava FinOps attribution between baseline (68.4% unassigned) and governed (3.4% unassigned).")
    parser.add_argument("--mode", choices=["governed", "baseline"], required=True, help="Target attribution state")
    parser.add_argument("--bind-suffix", help="Optional 12-char serving buildId suffix to bind local TMDL files to")
    parser.add_argument("--deploy", action="store_true", help="Switch ZavaFinOpsDemo semantic model in Fabric and refresh Direct Lake")
    args = parser.parse_args()

    if args.mode == "governed":
        summary = generate_governed_attribution()
    else:
        summary = restore_baseline_attribution()

    if args.bind_suffix:
        summary["tmdlTablesRebound"] = switch_semantic_model_build_suffix(args.bind_suffix[:12])
    elif args.deploy:
        summary["cloudDeployment"] = deploy_semantic_model_switch(args.mode)

    STATE_FILE.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
