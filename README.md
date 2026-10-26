# Enterprise FinOps & Platform Observability POC/Demo

A customer-owned FinOps, multi-platform observability, anomaly detection, and governed-action platform built natively on **Microsoft Fabric**, **OneLake**, **Direct Lake**, **Power BI (PBIR/TMDL)**, **Power Automate**, and **Fabric Data Agent**—spanning Azure Cost Management (FOCUS 1.0r2 & ActualCost), Azure Databricks & Unity Catalog, Microsoft Fabric FUAM, and Microsoft Foundry (Azure AI).

---

## Quick Links

- **[Customer Dev Setup & Deployment Guide](docs/customer-setup-guide.md)** — 5-minute sandbox quickstart (`--mode sample`), live data onboarding (`--mode live`), PBIR/TMDL rebinding, and custom RBAC setup.
- **[Executive Leave-Behind](docs/customer-executive-leave-behind.md)** — Reference architecture, semantic guardrails, and FinOps Hub/FOCUS comparison.
- **[Architecture Baseline](docs/architecture.md)** — Foundational design principles, source contracts, etc.
- **[Security Policy & Governance Boundaries](SECURITY.md)** — Least-privilege RBAC (`Microsoft.Resources/tags/read` & `write` only), `Merge`-only tag semantics, SSRF/ZipSlip hardening, and CodeQL gates.

---

## 5-Minute Quickstart (Clone, Test & Build)

```bash
# 1. Install dependencies
python -m venv .venv
# Windows: .\.venv\Scripts\Activate.ps1 | macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

# 2. Run all 48 unit & security validation tests
python -m unittest discover -s scripts -p "test_*.py" -v

# 3. Build the 30-table synthetic serving bundle and rebind the 8-page PBIR report locally
python scripts/deploy_customer_dev.py --mode sample --dry-run

# 4. Deploy to your own Fabric dev workspace (after populating config/customer.local.json)
cp config/customer.template.json config/customer.local.json
python scripts/deploy_customer_dev.py --config config/customer.local.json --mode sample
```

---

## What Is Included (8-Page Operating Experience & 30-Table Direct Lake Model)

The solution is authored in [cloud-dev/ZavaFinOpsOperatingDemo.pbip](cloud-dev/ZavaFinOpsOperatingDemo.pbip) and backed by the 30-table, 24-relationship, 69-measure Direct Lake semantic model [cloud-dev/ZavaFinOpsDemo.SemanticModel](cloud-dev/ZavaFinOpsDemo.SemanticModel/definition/model.tmdl):

1. **Page 1 — `Overview & Trust`:** 12 closed months of Azure billing (`$16,919.83` across `30,078` charge records and `854` resources) reconciled to `$0.0000` unexplained variance against native Azure Cost Management queries, with an explicit 4-decimal rounding bridge (`-$0.0861`) and reversible business-context attribution (`baseline` `68.4%` unassigned vs. `governed` `3.4%` unassigned).
2. **Page 2 — `Databricks & UC System Tables`:** Lakeview-style observability over `system.information_schema`, `system.billing.usage`, and `system.query.history` (`262` Unity Catalog objects across `3` catalogs and `29` schemas, `504.86` DBUs, and `4,526` SQL statements).
3. **Page 3 — `Fabric & FUAM System Tables`:** 1-to-1 visual symmetry for Microsoft Fabric powered by `FUAM_Lakehouse` (`39` workspaces, `532` active items across `31` artifact types, `5` capacity scopes, `1,021,021.69` Observed CUs, and `47,530` operations).
4. **Page 4 — `Microsoft Foundry & AI Usage`:** AI model and agent tool observability (`18` Foundry accounts/projects, `45` meters across `GPT-5.4`, `Sora 2`, `Image 2`, `GPT-4.1`, `GPT-5`, `Hosted vCPU/Memory`, `SRE Agent`, and `AI Evaluations`).
5. **Page 5 — `Usage & Investigation`:** Cross-platform operational investigation with a strict single-select platform control (`Databricks` vs. `Fabric`) that prevents mixing DBUs, CUs, and billed USD.
6. **Page 6 — `Anomalies & Advisor Insights`:** Surfaces the daily resource-group cost evaluation window behind Azure's `dailyanomalybyresourcegroup` (`ms:DailyAnomalyByResourceGroup`) scheduled email alerts (`1,011` daily RG rows), `FDPOAzureBudget` alerts, and `49` live `Microsoft.Advisor` recommendations (`56` unified ledger items).
7. **Page 7 — `Governance & Action`:** Bi-directional writeback featuring all `854` billed resources, interactive `textSlicer` Tag Key & Proposed Value editors, an explicit confirmation gate, a report-triggered Power Automate ARM `Merge` flow under a least-privilege custom RBAC operator, and a bookmark toggle to the `AssignBusinessContext` Fabric User Data Function (`business-context/function.py`).
8. **Page 8 — `Planning` + `ZavaFinOps_Agent`:** 3-month trailing-mean forecast (`$4,802.69` for Oct–Dec 2026) with rolling-origin Jul–Sep holdout backtesting (`MAE $458.70` vs. naive `$498.69`), sensitivity scenarios (`Baseline / ±10%`), and one-click launch buttons for the read-only grounded Fabric Data Agent (`ZavaFinOps_Agent`).

## Local Demo

Open [ZavaFinOps.pbip](ZavaFinOps.pbip) in Power BI Desktop. The two pages are:

- **Consolidated Billing:** platform totals, monthly billing, cross-platform initiatives and business-context attribution, including Unassigned costs.
- **Billing Detail:** resource/meter/charge drill-down and subscription-wide source reconciliation. The reconciliation respects the Period selection but intentionally ignores business and charge-detail filters.

Use the synchronized Business Unit and Initiative selectors to select **Finance / Finance Modernization**. This scenario links Fabric, Databricks and Purview, totaling **$1,496.3125** in the fixed-decimal model before display rounding. Ownership is fictional, not verified allocation or approved chargeback.

### Reproduce

Prerequisites: Python with `pyarrow` (verified with existing Python 3.12 and pyarrow 25.0.1), Node.js, and Power BI Desktop with PBIP support. The `powerbi-report-author` CLI is used for schema validation. The downloaded manifests and Parquet parts must remain under `.data/raw`; data and Desktop caches are ignored by Git.

```powershell
python -m unittest discover -s scripts -p 'test_*.py' -v
python scripts/prepare_billing.py
node scripts/build_report.mjs
powerbi-report-author validate ZavaFinOps.pbip
```

Open the project in Desktop and refresh if its local cache is absent or the prepared inputs changed. The model's `DataFolder` Power Query parameter currently points to this workspace's `.data/processed` directory; update it when moving the project. The report generator overwrites generated PBIR definitions, not semantic-model TMDL. Edit the generator for durable report changes. Use supported semantic-model tooling for model changes.

The [mapping configuration](config/demo-attribution.json) uses exact resource IDs and effective dates. Complete source business tags take precedence over fictional mappings.

## Architecture
[enterprise architecture](docs/architecture.md)

The platform is designed for investigation, accountability, optimization, and preventive controls, not only cost visualization. Current documentation supports Fabric capacity events and Microsoft Graph Copilot usage reports as ingestion candidates; customer-specific access, availability, completeness, and schema behavior still require validation.

## License
MIT License
