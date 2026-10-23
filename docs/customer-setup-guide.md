# Zava FinOps & Observability Platform — Customer Dev Setup & Deployment Guide

This guide walks customer cloud engineers, FinOps practitioners, and Cloud Solution Architects (CSAs) through deploying and customizing the **Zava FinOps & Observability Platform** in a customer **Microsoft Fabric** development environment.

---

## 1. Prerequisites

| Requirement | Minimum Version / SKU | Purpose |
| :--- | :--- | :--- |
| **Microsoft Fabric Capacity** | `F2` / `F4` (or Fabric Trial) | Hosts the `FinOps` workspace, `ZavaFinOpsFoundation` Lakehouse, Direct Lake semantic model, and Spark serving notebook. |
| **Python** | `3.11+` (`3.12` recommended) | Runs financial/operational adapters, unit tests, and the automated customer deployment CLI (`scripts/deploy_customer_dev.py`). |
| **Node.js** | `20+` | Runs the deterministic PBIR report generator (`scripts/build_operating_demo.mjs`). |
| **Azure CLI (`az`)** | `2.60+` | Authenticates to Azure Resource Manager, OneLake DFS, Fabric REST API, and Power BI REST API (`az login`). |
| **Power BI Authoring CLI** *(optional)* | `@microsoft/powerbi-report-author` | Validates generated PBIR report definitions locally (`powerbi-report-author validate`). |

### Install Local Dependencies
```bash
git clone <YOUR_REPO_URL>
cd Zava-FinOps

python -m venv .venv
# Windows PowerShell:
.\.venv\Scripts\Activate.ps1
# macOS / Linux:
# source .venv/bin/activate

pip install -r requirements.txt
```

---

## 2. Track A: 5-Minute Sandbox Quickstart (`--mode sample`)

Use **Sample Mode** first to verify your local toolchain or deploy a working 30-table Direct Lake semantic model and 8-page report into your Fabric dev workspace using the built-in synthetic dataset—before connecting live Azure billing exports or system tables.

### Step 1: Verify Local Build & Unit Tests (Offline / No Cloud Credentials Needed)
```bash
# 1. Run all 48 unit and security validation tests
python -m unittest discover -s scripts -p "test_*.py" -v

# 2. Run a dry-run deployment (builds the 30-table serving bundle and rebinds TMDL + PBIR locally)
python scripts/deploy_customer_dev.py --mode sample --dry-run
```

### Step 2: Configure Your Target Fabric Dev Workspace
1. In the [Microsoft Fabric Portal](https://app.fabric.microsoft.com), create (or select) a workspace assigned to an active Fabric capacity (e.g., `FinOps`).
2. Inside that workspace, create:
   - A **Lakehouse** (with Lakehouse schemas enabled) named `ZavaFinOpsFoundation`.
   - A blank **Notebook** named `ZavaFinOpsDemoServing` (bound to `ZavaFinOpsFoundation` as its default Lakehouse).
   - A blank **Semantic Model** (`ZavaFinOpsDemo`) and **Report** (`Zava FinOps Operating Demo`), or publish the initial `.pbip` from Power BI Desktop once to obtain their workspace item GUIDs.
3. Copy `config/customer.template.json` to `config/customer.local.json` (which is git-ignored):
   ```bash
   cp config/customer.template.json config/customer.local.json
   ```
4. Populate `config/customer.local.json` (or `.env` copied from `.env.example`) with your tenant domain, subscription ID, and Fabric item GUIDs:
   ```json
   {
     "tenantId": "<YOUR_ENTRA_TENANT_GUID>",
     "tenantDomain": "<yourtenant.onmicrosoft.com>",
     "subscriptionId": "<YOUR_AZURE_SUBSCRIPTION_GUID>",
     "fabric": {
       "workspaceId": "<YOUR_FABRIC_WORKSPACE_GUID>",
       "workspaceName": "FinOps",
       "capacityId": "<YOUR_FABRIC_CAPACITY_GUID>",
       "capacitySku": "F4",
       "lakehouseId": "<YOUR_LAKEHOUSE_GUID>",
       "lakehouseName": "ZavaFinOpsFoundation",
       "servingNotebookId": "<YOUR_NOTEBOOK_GUID>",
       "semanticModelId": "<YOUR_SEMANTIC_MODEL_GUID>",
       "semanticModelName": "ZavaFinOpsDemo",
       "reportId": "<YOUR_REPORT_GUID>",
       "reportName": "Zava FinOps Operating Demo",
       "dataAgentId": "<YOUR_OPTIONAL_DATA_AGENT_GUID>"
     }
   }
   ```

### Step 3: Deploy to Your Fabric Dev Workspace
```bash
az login --tenant <YOUR_ENTRA_TENANT_GUID>
python scripts/deploy_customer_dev.py --config config/customer.local.json --mode sample
```
This single command:
1. Builds the 30-table serving bundle (`demo_*_<buildId[:12]>.parquet`) and verifies SHA-256 digests.
2. Uploads the bundle and Python runners to `Files/demo-serving/<buildId>/input` in `ZavaFinOpsFoundation`.
3. Updates `ZavaFinOpsDemoServing` with single-executor Spark settings (`numExecutors: 1`) and runs the notebook job to materialize all 30 Delta tables in the `demo` schema.
4. Rebinds `cloud-dev/ZavaFinOpsDemo.SemanticModel` to your Lakehouse URL and table suffix, deploys the Direct Lake semantic model via the Fabric REST API, and triggers a dataset refresh.
5. Rebinds and publishes the 8-page `Zava FinOps Operating Demo` PBIR report to your workspace.

---

## 3. Track B: Connecting Customer Live Data (`--mode live`)

Once your sandbox workspace is running, onboard your real financial, platform, anomaly, and governance feeds incrementally.

### 3.1 Azure Cost Management Exports (Financial Foundation)
1. Export your closed monthly Azure billing snapshots (supports both **FOCUS 1.0r2 Parquet** and legacy **ActualCost 2021-10-01 CSV**).
2. Register and validate the monthly history against native Azure Cost Management totals (`±$0.0001` tolerance):
   ```bash
   python scripts/register_foundation_history.py --recovery .data/recovery/<date> --output .data/recovery/<date>/selection.json
   python scripts/finops_foundation.py --root .data/foundation/zava-demo build --selection .data/recovery/<date>/selection.json
   ```

### 3.2 Azure Databricks & Unity Catalog System Tables
1. Ensure your Databricks CLI profile has `SELECT` access to `system.billing.usage`, `system.query.history`, `system.compute.warehouses`, and `system.information_schema.tables` / `volumes`.
2. Update `config/databricks-ingestion.json` (or `databricks` in `config/customer.local.json`) with your `host`, `sourceWorkspaceId`, and SQL `warehouseId`, then run:
   ```bash
   python -c "from pathlib import Path; from databricks_medallion import extract, prepare; folder = extract(Path('config/databricks-ingestion.json'), Path('.data/databricks')); print(prepare(folder))"
   ```

### 3.3 Microsoft Fabric FUAM & Business Context SQL Database
1. **FUAM Lakehouse:** Deploy or connect to your existing [Fabric Unified Admin Monitoring (FUAM)](https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-unified-admin-monitoring) `FUAM_Lakehouse` SQL analytics endpoint and set `sqlEndpoints.fuamServer` in `config/customer.local.json`.
2. **Business Context SQL Database & User Data Function (UDF):**
   - Provision a Fabric SQL Database named `ZavaFinOpsBusinessContext`.
   - Execute the schema and view scripts in order:
     1. `business-context/001_assign_business_context.sql`
     2. `business-context/002_cloud_report_source.sql`
     3. `business-context/003_demo_fixture_and_effective_date_migration.sql`
   - Seed initial candidate resources and options:
     ```bash
     python scripts/deploy_business_context_data.py \
       --server <YOUR_FABRIC_SQL_ENDPOINT> \
       --database <YOUR_FABRIC_SQL_DB_NAME> \
       --tenant-id <YOUR_ENTRA_TENANT_GUID> \
       --actor-object-id <YOUR_ADMIN_ENTRA_OID>
     ```
   - Create a Fabric **User Data Function** item, paste `business-context/function.py`, bind a connection alias named `BusinessContextSql` to `ZavaFinOpsBusinessContext`, and publish.

### 3.4 Daily Cost Anomalies, Azure Advisor & Microsoft Foundry AI Usage
- **Anomalies & Advisor (`.data/databricks/anomalies-and-advisor-snapshot.json`):** Populate from `Microsoft.CostManagement/scheduledActions` (`ms:DailyAnomalyByResourceGroup` daily query grouped by `ResourceGroupName`), `Microsoft.CostManagement/alerts`, and `Microsoft.Advisor/recommendations`.
- **Microsoft Foundry AI Usage (`.data/databricks/foundry-cost-snapshot.json`):** Populate daily meter rows (`MeterCategory IN ('Foundry Models', 'Foundry Tools')`) with `UsageDate`, `ResourceId`, `ResourceGroupName`, `ResourceLocation`, `ServiceName`, `Meter`, and `CostUSD`.

### 3.5 Scoped Native Azure Tagging (Power Automate + Least-Privilege RBAC)
1. Create a custom Azure RBAC role granting **only** `Microsoft.Resources/tags/read` and `Microsoft.Resources/tags/write` on your target subscription or resource group:
   ```bash
   az role definition create --role-definition '{
     "Name": "Zava FinOps Demo Tag Operator",
     "Description": "Read and merge resource tags only; no compute, data, or delete permissions.",
     "Actions": ["Microsoft.Resources/tags/read", "Microsoft.Resources/tags/write"],
     "NotActions": [],
     "DataActions": [],
     "NotDataActions": [],
     "AssignableScopes": ["/subscriptions/<YOUR_SUBSCRIPTION_ID>/resourceGroups/<YOUR_APPROVED_RG>"]
   }'
   ```
2. Assign the role to a dedicated service principal or operator account (`tagOperatorUpn`).
3. In Power Automate, create a Power BI button-triggered flow that validates `Confirmation == "Confirmed"`, checks the target resource against your allowlist, and sends an HTTP `PATCH` (`{"operation": "Merge", "properties": {"tags": ...}}`) to `https://management.azure.com<ResourceId>/providers/Microsoft.Resources/tags/default?api-version=2021-04-01`.
4. Put your `powerAutomateEnvironmentId` and `powerAutomateFlowId` into `config/customer.local.json` and run:
   ```bash
   python scripts/deploy_customer_dev.py --config config/customer.local.json --mode live
   ```

---

## 4. Toggling Between Baseline and Governed Attribution Modes

For live customer demonstrations, you can toggle between **Baseline Mode** (`68.4%` unassigned spend) and **Governed Mode** (`96.6%` attributed across 6 Business Units / `3.4%` unassigned tail) at any time:

```bash
# Switch to Governed Mode (96.6% attributed):
python scripts/manage_attribution_coverage.py --mode governed

# Revert to Baseline Mode (68.4% unassigned):
python scripts/manage_attribution_coverage.py --mode baseline
```

---

## 5. Troubleshooting & Common Questions

- **Direct Lake Refresh Fails with `ModelRefresh_ShortMessage_ProcessingError`:**
  Ensure that `ZavaFinOpsDemoServing` completed its Spark run (`status: "Completed"`) and that `fabric.workspaceId` and `fabric.lakehouseId` in `config/customer.local.json` match the Lakehouse where `demo.demo_*_<suffix>` tables were materialized.
- **Power BI Desktop Shows Blank Visuals After Adding a New Table:**
  When `definition.pbir` uses a live XMLA connection (`byConnection`), restart Power BI Desktop after refreshing the cloud semantic model so Desktop refreshes its cached model schema.
- **Power Automate Visual Warning During `powerbi-report-author validate`:**
  The single warning `PBIR_VISUAL_TYPE_UNKNOWN` for `FlowVisual_C29F1DCC_81F5_4973_94AD_0517D44CC06A` is expected because Power Automate is an AppSource custom visual rather than a core built-in visual.
