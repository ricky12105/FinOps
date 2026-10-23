# Security Policy & Governance Boundaries

## 1. Security Architecture Principles

The **Zava FinOps & Platform ObservabilityPOC** is designed for customer-owned deployment inside the customer's own Microsoft Entra tenant, Azure subscriptions, and Microsoft Fabric workspaces. No telemetry, billing records, or credentials are transmitted to external third-party SaaS services.

### 1.1 Strict Separation of Identities
The architecture enforces four distinct identity boundaries:
1. **Report Viewer Identity:** Standard Power BI / Fabric consumers who view reconciled financial and operational metrics subject to workspace and semantic-model access controls.
2. **Business Context Caller Identity:** Authenticated Fabric users invoking the `AssignBusinessContext` Fabric User Data Function (`business-context/function.py`). The UDF extracts caller `Oid` and `TenantId` directly from `UserDataFunctionContext.executing_user` (never from client-supplied parameters), validates resource/option grants in `dbo.BusinessContextGrant`, enforces optimistic concurrency (`ResourceVersion`), and writes an immutable audit trail.
3. **Native Azure Tag Operator Identity:** Report-triggered Azure tag mutations execute through a dedicated, least-privilege Entra identity bound to a custom Azure RBAC role (`Zava FinOps Demo Tag Operator`) that grants **only**:
   - `Microsoft.Resources/tags/read`
   - `Microsoft.Resources/tags/write`
   Zero compute, storage, data-plane, role-assignment, or resource-deletion permissions are granted.
4. **Grounded Data Agent Identity (`ZavaFinOps_Agent`):** Strictly **read-only** over the curated `ZavaFinOpsDemo` Direct Lake semantic model. No write tools, UDFs, or ARM connectors are exposed to the conversational agent.

### 1.2 Safe Tag Mutation Contract (`Merge`-Only)
- All Azure tag updates use `PATCH .../providers/Microsoft.Resources/tags/default?api-version=2021-04-01` with `"operation": "Merge"`.
- Unrelated existing tags on target resources are preserved; tag deletions and full-resource `PUT` replacements are blocked.
- Explicit user confirmation (`Confirmation == "I confirm this exact scoped change"`) is required before execution.

### 1.3 CodeQL & Input Hardening
All Python, Node.js, and PowerShell scripts enforce:
- **SSRF Prevention (`finops_config.validate_https_url`):** Outbound REST calls require `https://`, reject embedded credentials, and validate hostnames against an explicit Microsoft cloud host allowlist (`api.fabric.microsoft.com`, `api.powerbi.com`, `onelake.dfs.fabric.microsoft.com`, `management.azure.com`).
- **Archive & Path Traversal Protection (`finops_config.safe_extract_path`):** All ZIP bundle extractions verify flat filenames and enforce `target.resolve().is_relative_to(destination.resolve())`.
- **Subprocess & Code Generation Hardening:** CLI invocations use `shell=False` with validated UUID/SHA-256 arguments (`validate_uuid`, `validate_sha256`).
- **Read-Only SQL Extraction Gates:** Operational SQL adapters reject any statement that does not begin with `SELECT` and enforce parameterized queries for SQL writeback procedures.

## 2. Secret & Configuration Hygiene
- **Never commit secrets, access tokens, or `.env` files.**
- Customer-specific tenant IDs, subscription IDs, workspace IDs, and SQL endpoints belong in `.env` or `config/customer.local.json` (both excluded via `.gitignore`).
- Committed configuration files (`config/*.json`, `config/customer.template.json`, `.env.example`) contain only sanitized synthetic placeholder GUIDs (`00000000-0000-0000-0000-...`) and example domains (`contoso.onmicrosoft.com`).

## 3. Reporting a Vulnerability
If you discover a security issue in this repository, please report it privately to the repository maintainers via GitHub Security Advisories rather than opening a public issue.
