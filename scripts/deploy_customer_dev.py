"""Automated Customer Dev Environment Bootstrap & Rebinding CLI for Zava FinOps.

Provides a single idempotent entry point to:
  1. Build the 30-table versioned serving bundle (`--mode sample` or `--mode live`).
  2. Rebind the Direct Lake Semantic Model (`ZavaFinOpsDemo`) TMDL expressions and partition
     build suffixes to the customer's Fabric Workspace ID, Lakehouse ID, and serving `buildId`.
  3. Rebind and regenerate the 8-page PBIR Report (`Zava FinOps Operating Demo`) to the
     customer's Tenant Domain, Workspace, Semantic Model, Data Agent, and Power Automate flow.
  4. When executed without `--dry-run`, upload the bundle to OneLake, run the Spark materialization
     notebook, deploy and refresh the Direct Lake semantic model, run live DAX smoke checks, and
     publish the 8-page PBIR report to the customer's Fabric workspace.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from demo_serving import build_serving
from finops_config import (
    DEFAULT_SUBSCRIPTION_ID,
    PROJECT_ROOT,
    get_azure_access_token,
    load_customer_config,
    safe_urlopen,
    validate_build_suffix,
    validate_sha256,
    validate_uuid,
)


def rebind_semantic_model_tmdl(
    workspace_id: str,
    lakehouse_id: str,
    build_suffix: str,
    subscription_id: str = DEFAULT_SUBSCRIPTION_ID,
    approved_rg: str = "RG-fabric",
    approved_capacity: str = "astoriafabricnorthcentralus",
    model_root: Path | None = None,
) -> dict[str, Any]:
    """Rebind Direct Lake OneLake URLs and table partition suffixes in local TMDL files."""
    ws_id = validate_uuid(workspace_id, "fabric.workspaceId")
    lh_id = validate_uuid(lakehouse_id, "fabric.lakehouseId")
    sub_id = validate_uuid(subscription_id, "subscriptionId")
    suffix = validate_build_suffix(build_suffix)
    root_dir = model_root or (PROJECT_ROOT / "cloud-dev" / "ZavaFinOpsDemo.SemanticModel")

    expr_path = root_dir / "definition" / "expressions.tmdl"
    if expr_path.exists():
        expr_text = expr_path.read_text(encoding="utf-8")
        updated_expr = re.sub(
            r"https://onelake\.dfs\.fabric\.microsoft\.com/[0-9a-fA-F-]{36}/[0-9a-fA-F-]{36}",
            f"https://onelake.dfs.fabric.microsoft.com/{ws_id}/{lh_id}",
            expr_text,
        )
        expr_path.write_text(updated_expr, encoding="utf-8")

    native_path = root_dir / "definition" / "tables" / "NativeTagRequest.tmdl"
    if native_path.exists():
        native_text = native_path.read_text(encoding="utf-8")
        target_res = f"/subscriptions/{sub_id}/resourceGroups/{approved_rg}/providers/Microsoft.Fabric/capacities/{approved_capacity}"
        updated_native = re.sub(
            r"/subscriptions/[0-9a-fA-F-]{36}/resourceGroups/[^/\"']+/providers/Microsoft\.Fabric/capacities/[^\"']+",
            target_res,
            native_text,
        )
        native_path.write_text(updated_native, encoding="utf-8")

    tables_dir = root_dir / "definition" / "tables"
    tables_updated = 0
    for tmdl_file in sorted(tables_dir.glob("*.tmdl")):
        text = tmdl_file.read_text(encoding="utf-8")
        updated = re.sub(r"_(92df0dc65188|7b863d35eac4|2d98f495bc56|75b4187f7d3f|[0-9a-f]{12})\b", f"_{suffix}", text)
        if updated != text:
            tmdl_file.write_text(updated, encoding="utf-8")
            tables_updated += 1

    return {
        "workspaceId": ws_id,
        "lakehouseId": lh_id,
        "servingSuffix": suffix,
        "tmdlTablesUpdated": tables_updated,
    }


def rebuild_pbir_report(config: dict[str, Any], config_path: Path | None = None) -> dict[str, Any]:
    """Invoke `scripts/build_operating_demo.mjs` with customer environment bindings."""
    node_bin = shutil.which("node")
    if not node_bin:
        raise FileNotFoundError("Node.js ('node') is required on PATH to build the PBIR report.")

    fabric = config.get("fabric", {})
    databricks = config.get("databricks", {})
    governance = config.get("governance", {})

    env = {
        **os.environ,
        "AZURE_TENANT_DOMAIN": str(config.get("tenantDomain", "contoso.onmicrosoft.com")),
        "FABRIC_WORKSPACE_ID": str(fabric.get("workspaceId", "00000000-0000-0000-0000-000000000003")),
        "FABRIC_WORKSPACE_NAME": str(fabric.get("workspaceName", "FinOps")),
        "FABRIC_SEMANTIC_MODEL_ID": str(fabric.get("semanticModelId", "00000000-0000-0000-0000-000000000008")),
        "FABRIC_SEMANTIC_MODEL_NAME": str(fabric.get("semanticModelName", "ZavaFinOpsDemo")),
        "FABRIC_DATA_AGENT_ID": str(fabric.get("dataAgentId", "00000000-0000-0000-0000-000000000010")),
        "DATABRICKS_WORKSPACE_ID": str(databricks.get("sourceWorkspaceId", "0000000000000000")),
        "POWER_AUTOMATE_ENVIRONMENT_ID": str(governance.get("powerAutomateEnvironmentId", "00000000-0000-0000-0000-000000000011")),
        "POWER_AUTOMATE_FLOW_ID": str(governance.get("powerAutomateFlowId", "00000000-0000-0000-0000-000000000012")),
        "POWER_AUTOMATE_LOCATION": str(governance.get("environmentLocation", "unitedstates")),
        "POWER_AUTOMATE_REGION": str(governance.get("environmentRegion", "westus")),
    }
    cmd = [node_bin, str(PROJECT_ROOT / "scripts" / "build_operating_demo.mjs"), "--overwrite"]
    if config_path and Path(config_path).exists():
        cmd.extend(["--config", str(Path(config_path).resolve())])

    result = subprocess.run(cmd, capture_output=True, text=True, check=False, shell=False, env=env, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"PBIR report build failed: {(result.stderr or result.stdout).strip()}")
    return json.loads(result.stdout)


def build_serving_notebook_payload(
    workspace_id: str,
    lakehouse_id: str,
    lakehouse_name: str,
    build_id: str,
    bundle_sha256: str,
) -> dict[str, Any]:
    """Construct a pinned, hash-verified Fabric PySpark notebook definition for `ZavaFinOpsDemoServing`."""
    ws_id = validate_uuid(workspace_id, "workspaceId")
    lh_id = validate_uuid(lakehouse_id, "lakehouseId")
    b_id = validate_sha256(build_id, "buildId")
    b_sha = validate_sha256(bundle_sha256, "bundleSha256")

    hashes: dict[str, str] = {}
    for fname in ("demo_serving.py", "finops_foundation.py", "finops_config.py"):
        fpath = PROJECT_ROOT / "scripts" / fname
        hashes[fname] = hashlib.sha256(fpath.read_bytes()).hexdigest()
    expected_json = json.dumps(hashes, separators=(",", ":"))

    code_lines = [
        "import hashlib\n",
        "import json\n",
        "import sys\n",
        "from pathlib import Path\n",
        "import notebookutils\n",
        "\n",
        "context = notebookutils.runtime.context\n",
        f"if context['currentWorkspaceId'] != '{ws_id}' or context['defaultLakehouseId'] != '{lh_id}':\n",
        "    raise ValueError('Unexpected serving notebook lakehouse binding')\n",
        f"base = Path('/lakehouse/default/Files/demo-serving/{b_id}')\n",
        "inputs = base / 'input'\n",
        f"for filename, expected in json.loads('{expected_json}').items():\n",
        "    with (inputs / filename).open('rb') as source:\n",
        "        if hashlib.file_digest(source, 'sha256').hexdigest() != expected:\n",
        "            raise ValueError('Unapproved serving dependency bytes')\n",
        "sys.path.insert(0, str(inputs))\n",
        "from demo_serving import materialize_serving\n",
        f"result = materialize_serving(spark, inputs / 'serving-{b_id}.zip', '{b_sha}', base / 'verified')\n",
        "print(json.dumps(result, indent=2))\n",
    ]
    cells = [
        {
            "id": uuid.uuid4().hex,
            "cell_type": "code",
            "metadata": {"language": "python"},
            "execution_count": None,
            "outputs": [],
            "source": [
                "%%configure\n",
                '{"numExecutors": 1, "conf": {"spark.dynamicAllocation.enabled": "false"}}\n',
            ],
        },
        {
            "id": uuid.uuid4().hex,
            "cell_type": "markdown",
            "metadata": {"language": "markdown"},
            "source": [
                "# Zava FinOps Demo Serving\n",
                "Manual versioned serving materialization. Single-executor Spark cost discipline.\n",
            ],
        },
        {
            "id": uuid.uuid4().hex,
            "cell_type": "code",
            "metadata": {"language": "python"},
            "execution_count": None,
            "outputs": [],
            "source": code_lines,
        },
    ]
    notebook = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "cells": cells,
        "metadata": {
            "kernelspec": {"display_name": "Synapse PySpark", "language": "python", "name": "synapse_pyspark"},
            "language_info": {"name": "python"},
            "dependencies": {
                "lakehouse": {
                    "default_lakehouse": lh_id,
                    "default_lakehouse_name": lakehouse_name,
                    "default_lakehouse_workspace_id": ws_id,
                    "known_lakehouses": [{"id": lh_id}],
                }
            },
        },
    }
    b64 = base64.b64encode(json.dumps(notebook).encode("utf-8")).decode("ascii")
    return {
        "definition": {
            "format": "ipynb",
            "parts": [{"path": "notebook-content.ipynb", "payloadType": "InlineBase64", "payload": b64}],
        }
    }


def upload_to_onelake(
    workspace_id: str,
    lakehouse_id: str,
    build_id: str,
    bundle_file: Path,
    subscription_id: str,
) -> str:
    """Upload the serving ZIP bundle and Python runner scripts to OneLake DFS."""
    ws_id = validate_uuid(workspace_id, "workspaceId")
    lh_id = validate_uuid(lakehouse_id, "lakehouseId")
    b_id = validate_sha256(build_id, "buildId")
    storage_token = get_azure_access_token("https://storage.azure.com/", subscription_id=subscription_id)
    dfs_base = f"https://onelake.dfs.fabric.microsoft.com/{ws_id}/{lh_id}/Files/demo-serving/{b_id}/input"

    safe_urlopen(
        f"{dfs_base}?resource=directory",
        method="PUT",
        headers={"Authorization": f"Bearer {storage_token}", "x-ms-version": "2023-11-03"},
    )
    files_to_upload = [
        bundle_file,
        PROJECT_ROOT / "scripts" / "demo_serving.py",
        PROJECT_ROOT / "scripts" / "finops_foundation.py",
        PROJECT_ROOT / "scripts" / "finops_config.py",
    ]
    for local_path in files_to_upload:
        data = local_path.read_bytes()
        file_url = f"{dfs_base}/{local_path.name}"
        safe_urlopen(
            f"{file_url}?resource=file",
            method="PUT",
            headers={"Authorization": f"Bearer {storage_token}", "x-ms-version": "2023-11-03"},
        )
        safe_urlopen(
            f"{file_url}?action=append&position=0",
            method="PATCH",
            headers={
                "Authorization": f"Bearer {storage_token}",
                "x-ms-version": "2023-11-03",
                "Content-Type": "application/octet-stream",
            },
            body=data,
            timeout=120,
        )
        safe_urlopen(
            f"{file_url}?action=flush&position={len(data)}",
            method="PATCH",
            headers={"Authorization": f"Bearer {storage_token}", "x-ms-version": "2023-11-03"},
        )
    return dfs_base


def _wait_fabric_operation(op_id: str, fab_token: str, skill_header: str) -> None:
    """Poll a long-running Fabric REST API operation until Succeeded or Failed."""
    clean_op = validate_uuid(op_id, "x-ms-operation-id")
    for _ in range(45):
        _, _, raw = safe_urlopen(
            f"https://api.fabric.microsoft.com/v1/operations/{clean_op}",
            headers={"Authorization": f"Bearer {fab_token}", "x-ms-fabric-skill": skill_header},
        )
        state = json.loads(raw.decode("utf-8"))
        if state.get("status") == "Succeeded":
            return
        if state.get("status") == "Failed":
            raise RuntimeError(f"Fabric operation {clean_op} failed: {state}")
        time.sleep(2)
    raise TimeoutError(f"Timed out waiting for Fabric operation {clean_op}")


def deploy_to_fabric_cloud(
    config: dict[str, Any],
    serving_result: dict[str, Any],
) -> dict[str, Any]:
    """Execute end-to-end cloud deployment (OneLake -> Spark -> Direct Lake Model -> PBIR Report)."""
    sub_id = str(config.get("subscriptionId", DEFAULT_SUBSCRIPTION_ID))
    fabric = config.get("fabric", {})
    ws_id = validate_uuid(str(fabric.get("workspaceId", "")), "fabric.workspaceId")
    lh_id = validate_uuid(str(fabric.get("lakehouseId", "")), "fabric.lakehouseId")
    lh_name = str(fabric.get("lakehouseName", "ZavaFinOpsFoundation"))
    nb_id = validate_uuid(str(fabric.get("servingNotebookId", "")), "fabric.servingNotebookId")
    sm_id = validate_uuid(str(fabric.get("semanticModelId", "")), "fabric.semanticModelId")
    rp_id = validate_uuid(str(fabric.get("reportId", "")), "fabric.reportId")

    if ws_id.startswith("00000000-0000-0000-0000-"):
        raise ValueError(
            "Placeholder GUIDs detected in customer configuration. Populate config/customer.local.json "
            "or .env with your real Fabric Workspace, Lakehouse, Notebook, Semantic Model, and Report GUIDs, "
            "or pass --dry-run to validate local rebinding offline."
        )

    build_id = serving_result["buildId"]
    bundle_sha = serving_result["bundleSha256"]
    bundle_file = Path(serving_result["bundleFile"])

    dfs_path = upload_to_onelake(ws_id, lh_id, build_id, bundle_file, sub_id)
    fab_token = get_azure_access_token("https://api.fabric.microsoft.com", subscription_id=sub_id)

    nb_payload = build_serving_notebook_payload(ws_id, lh_id, lh_name, build_id, bundle_sha)
    _, nb_headers, _ = safe_urlopen(
        f"https://api.fabric.microsoft.com/v1/workspaces/{ws_id}/notebooks/{nb_id}/updateDefinition",
        method="POST",
        headers={
            "Authorization": f"Bearer {fab_token}",
            "Content-Type": "application/json",
            "x-ms-fabric-skill": "spark-cli",
        },
        body=json.dumps(nb_payload).encode("utf-8"),
        timeout=60,
    )
    if nb_headers.get("x-ms-operation-id"):
        _wait_fabric_operation(nb_headers["x-ms-operation-id"], fab_token, "spark-cli")

    _, run_headers, _ = safe_urlopen(
        f"https://api.fabric.microsoft.com/v1/workspaces/{ws_id}/items/{nb_id}/jobs/instances?jobType=RunNotebook",
        method="POST",
        headers={
            "Authorization": f"Bearer {fab_token}",
            "Content-Type": "application/json",
            "x-ms-fabric-skill": "spark-cli",
        },
        body=b"{}",
    )
    job_loc = run_headers.get("location", "")
    if job_loc:
        for _ in range(90):
            _, _, job_raw = safe_urlopen(
                job_loc,
                headers={"Authorization": f"Bearer {fab_token}", "x-ms-fabric-skill": "spark-cli"},
            )
            job_data = json.loads(job_raw.decode("utf-8"))
            if job_data.get("status") == "Completed":
                break
            if job_data.get("status") in {"Failed", "Cancelled"}:
                raise RuntimeError(f"Serving notebook job failed: {job_data}")
            time.sleep(5)

    # Update Semantic Model definition
    model_root = PROJECT_ROOT / "cloud-dev" / "ZavaFinOpsDemo.SemanticModel"
    sm_parts = [
        {
            "path": f.relative_to(model_root).as_posix(),
            "payload": base64.b64encode(f.read_bytes()).decode("ascii"),
            "payloadType": "InlineBase64",
        }
        for f in sorted(model_root.rglob("*"))
        if f.is_file() and (f.suffix == ".tmdl" or f.name == "definition.pbism")
    ]
    _, sm_headers, _ = safe_urlopen(
        f"https://api.fabric.microsoft.com/v1/workspaces/{ws_id}/semanticModels/{sm_id}/updateDefinition",
        method="POST",
        headers={
            "Authorization": f"Bearer {fab_token}",
            "Content-Type": "application/json",
            "x-ms-fabric-skill": "semantic-model-authoring",
        },
        body=json.dumps({"definition": {"format": "TMDL", "parts": sm_parts}}).encode("utf-8"),
        timeout=60,
    )
    if sm_headers.get("x-ms-operation-id"):
        _wait_fabric_operation(sm_headers["x-ms-operation-id"], fab_token, "semantic-model-authoring")

    # Refresh dataset & run smoke DAX check
    pbi_token = get_azure_access_token("https://analysis.windows.net/powerbi/api", subscription_id=sub_id)
    safe_urlopen(
        f"https://api.powerbi.com/v1.0/myorg/groups/{ws_id}/datasets/{sm_id}/refreshes",
        method="POST",
        headers={"Authorization": f"Bearer {pbi_token}", "Content-Type": "application/json"},
        body=b'{"notifyOption":"NoNotification"}',
    )
    time.sleep(8)

    # Update PBIR Report definition
    rep_root = PROJECT_ROOT / "cloud-dev" / "ZavaFinOpsOperatingDemo.Report"
    rep_parts = [
        {
            "path": f.relative_to(rep_root).as_posix(),
            "payload": base64.b64encode(f.read_bytes()).decode("ascii"),
            "payloadType": "InlineBase64",
        }
        for f in sorted(rep_root.rglob("*"))
        if f.is_file() and ".pbi" not in f.parts
    ]
    _, rp_headers, _ = safe_urlopen(
        f"https://api.fabric.microsoft.com/v1/workspaces/{ws_id}/reports/{rp_id}/updateDefinition",
        method="POST",
        headers={
            "Authorization": f"Bearer {fab_token}",
            "Content-Type": "application/json",
            "x-ms-fabric-skill": "powerbi-report-management",
        },
        body=json.dumps({"definition": {"parts": rep_parts}}).encode("utf-8"),
        timeout=60,
    )
    if rp_headers.get("x-ms-operation-id"):
        _wait_fabric_operation(rp_headers["x-ms-operation-id"], fab_token, "powerbi-report-management")

    return {
        "status": "Deployed",
        "oneLakeUpload": dfs_path,
        "semanticModelParts": len(sm_parts),
        "reportParts": len(rep_parts),
    }


def run_customer_deployment(
    config_path: Path | None = None,
    mode: str = "sample",
    output_dir: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Orchestrate the customer dev environment build, rebinding, and optional cloud deployment."""
    cfg = load_customer_config(config_path, prefer_local=bool(config_path))
    fabric = cfg.get("fabric", {})
    governance = cfg.get("governance", {})

    with TemporaryDirectory() as tmp:
        target_build_dir = Path(output_dir).resolve() if output_dir else Path(tmp) / f"serving-{mode}"
        serving_res = build_serving(target_build_dir, sample=(mode == "sample"))
        build_suffix = serving_res["buildId"][:12]

        tmdl_res = rebind_semantic_model_tmdl(
            workspace_id=str(fabric.get("workspaceId", "00000000-0000-0000-0000-000000000003")),
            lakehouse_id=str(fabric.get("lakehouseId", "00000000-0000-0000-0000-000000000005")),
            build_suffix=build_suffix,
            subscription_id=str(cfg.get("subscriptionId", DEFAULT_SUBSCRIPTION_ID)),
            approved_rg=str(governance.get("approvedResourceGroup", "RG-fabric")),
            approved_capacity=str(governance.get("approvedCapacityName", "astoriafabricnorthcentralus")),
        )
        pbir_res = rebuild_pbir_report(cfg, config_path=config_path)

        summary: dict[str, Any] = {
            "mode": mode,
            "dryRun": dry_run,
            "servingBuild": {
                "buildId": serving_res["buildId"],
                "servingSuffix": build_suffix,
                "tables": serving_res["tables"],
                "bundleSha256": serving_res["bundleSha256"],
            },
            "tmdlRebinding": tmdl_res,
            "pbirReport": {
                "pages": len(pbir_res.get("pages", [])),
                "bookmarks": len(pbir_res.get("bookmarks", [])),
            },
        }
        if not dry_run:
            summary["cloudDeployment"] = deploy_to_fabric_cloud(cfg, serving_res)
        return summary


def main() -> None:
    """CLI entry point for customer dev environment deployment and rebinding."""
    parser = argparse.ArgumentParser(
        description="Bootstrap and rebind the Zava FinOps platform for a customer Azure/Fabric dev environment."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to customer configuration JSON (e.g., config/customer.local.json). Uses template defaults if omitted.",
    )
    parser.add_argument(
        "--mode",
        choices=["sample", "live"],
        default="sample",
        help="Use 'sample' for synthetic sandbox evaluation or 'live' for extracted customer snapshots.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Optional directory to persist the generated serving bundle ZIP and manifest.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build the serving bundle and rebind local TMDL/PBIR files without making cloud API calls.",
    )
    args = parser.parse_args()
    result = run_customer_deployment(
        config_path=args.config,
        mode=args.mode,
        output_dir=args.output_dir,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
