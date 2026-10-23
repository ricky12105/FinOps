# Assign Business Context

This is the backend slice for the proposed Fabric write-back increment. It is not connected to the local v0.1 Import report and does not provision Fabric resources.

## Flow

1. A curated sync publishes resource candidates and controlled business-context options to SQL. Resources with complete source tags are not eligible.
2. An administrator grants a user by Entra object ID, tenant, exact resource, allowed context option, and effective-date window.
3. A published Fabric User Data Function receives the selected resource, context option, effective date, reason, and version. It derives actor and tenant from Fabric's verified invocation context.
4. The function calls the SQL procedure. The procedure checks eligibility, exact grant, date, and expected version while holding a resource-row lock, then appends an assignment and advances the version in one transaction.
5. The report reads the effective-dated current view. Import mode requires refresh; a supported DirectQuery/Direct Lake design can provide fresher visibility.

## Deploy prerequisites

- A customer-approved Fabric workspace and SQL database; no workspace/capacity is created by these files.
- Run `001_assign_business_context.sql` in the target SQL database.
- Publish `function.py` as a Fabric User Data Function, add a SQL connection with alias `BusinessContextSql`, and grant the function connection identity only `EXECUTE` on `dbo.AssignBusinessContext` (ownership chaining gives the procedure access to its tables).
- Grant intended users **Execute functions** on the function item. The UDF uses `UserDataFunctionContext` OID and tenant, not caller-supplied identity parameters.
- Provision resources, options, and exact user/resource/option grants through an administrator-owned controlled process. Do not expose write access to those tables through the report.
- Configure the report's Data Function button with the function's parameters and refresh action. It can only be invoked in the published Power BI experience; test in the customer workspace before broad release.

The database connection uses its configured connection identity. The SQL procedure is the only write surface exposed to that identity. The invoking user's verified identity is passed separately for row-level authorization. SQL administrators and workspace administrators remain privileged principals and require separate governance.

## Rules and limits

- Resource keys must exist in the server-maintained candidate list; resource ID is not accepted from the client.
- Complete native source tags take precedence and block manual assignment.
- Context choices and actor/resource grants are explicit and effective-dated.
- Assignments are append-only and may take effect today or later; retroactive edits are rejected. A correction requires an approved administrative process.
- `expectedVersion` rejects stale report selections. A per-resource transaction lock serializes competing submissions.
- Fabric invocation ID and request hash make a replay idempotent; a stale second invocation is rejected.
- The assignment does not mutate native Azure or Databricks tags, allocate shared capacity costs, or certify chargeback.
- This increment has not been deployed or integration-tested against Fabric. SQL concurrency behavior and the configured connection permissions must be verified in the target database before production use.

## References

- [Fabric UDF programming model](https://learn.microsoft.com/fabric/data-engineering/user-data-functions/python-programming-model)
- [Connect a UDF to Fabric data sources](https://learn.microsoft.com/fabric/data-engineering/user-data-functions/connect-to-data-sources)
- [Power BI translytical task flow tutorial](https://learn.microsoft.com/power-bi/create-reports/translytical-task-flow-tutorial)