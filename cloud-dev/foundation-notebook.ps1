<#
.SYNOPSIS
    Generates pinned, hash-verified Microsoft Fabric PySpark notebook definitions (.ipynb Base64 payloads).
.DESCRIPTION
    Constructs Fabric REST API `updateDefinition` / `createItem` payloads for:
      - `ZavaFinOpsDemoServing` (`New-DemoServingNotebookDefinition`)
      - `ZavaFinOpsFoundationValidate` (`New-FoundationNotebookDefinition`)
      - `ZavaDatabricksMedallion` (`New-DatabricksMedallionNotebookDefinition`)
    Enforces single-executor Spark cost discipline (`numExecutors: 1`, `dynamicAllocation: false`)
    and validates all interpolated GUIDs and SHA-256 digests against strict regexes to prevent
    code injection into generated Python cells.
#>

function Assert-SafeIdentifier {
    param(
        [Parameter(Mandatory)][string]$Value,
        [Parameter(Mandatory)][string]$Pattern,
        [Parameter(Mandatory)][string]$Name
    )
    if ($Value -notmatch $Pattern) {
        throw "Unsafe or malformed $Name rejected: '$Value'"
    }
    return $Value
}

function New-DemoServingNotebookDefinition {
    param(
        [Parameter(Mandatory)][string]$ProjectRoot,
        [Parameter(Mandatory)][string]$ManifestPath,
        [Parameter(Mandatory)][string]$BundleSha256,
        [string]$InputRevision = ''
    )

    $manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
    $deployment = Get-Content -LiteralPath (Join-Path $ProjectRoot 'config/foundation-deployment.json') -Raw | ConvertFrom-Json
    [void](Assert-SafeIdentifier -Value $deployment.workspaceId -Pattern '^[0-9a-fA-F-]{36}$' -Name 'workspaceId')
    [void](Assert-SafeIdentifier -Value $deployment.lakehouseId -Pattern '^[0-9a-fA-F-]{36}$' -Name 'lakehouseId')
    [void](Assert-SafeIdentifier -Value $manifest.buildId -Pattern '^[0-9a-fA-F]{64}$' -Name 'buildId')
    [void](Assert-SafeIdentifier -Value $BundleSha256 -Pattern '^[0-9a-fA-F]{64}$' -Name 'BundleSha256')
    if ($InputRevision -and $InputRevision -notmatch '^[A-Za-z0-9_.-]+$') {
        throw "Unsafe InputRevision rejected: '$InputRevision'"
    }
    $hashes = @{}
    foreach ($filename in @('demo_serving.py', 'finops_foundation.py')) {
        $hashes[$filename] = (Get-FileHash -LiteralPath (Join-Path $ProjectRoot "scripts/$filename") -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    $expected = $hashes | ConvertTo-Json -Compress
    $code = @"
import hashlib
import json
import sys
from pathlib import Path
import notebookutils

context = notebookutils.runtime.context
if context['currentWorkspaceId'] != '$($deployment.workspaceId)' or context['defaultLakehouseId'] != '$($deployment.lakehouseId)':
    raise ValueError('Unexpected serving notebook lakehouse binding')
base = Path('/lakehouse/default/Files/demo-serving/$($manifest.buildId)')
inputs = base / 'input' / '$InputRevision'
for filename, expected in json.loads('$expected').items():
    with (inputs / filename).open('rb') as source:
        if hashlib.file_digest(source, 'sha256').hexdigest() != expected:
            raise ValueError('Unapproved serving dependency bytes')
sys.path.insert(0, str(inputs))
from demo_serving import materialize_serving
result = materialize_serving(spark, base / 'input' / 'serving-$($manifest.buildId).zip', '$BundleSha256', base / 'verified')
print(json.dumps(result, indent=2))
"@
    $cells = @(
        @{cell_type='code';metadata=@{};execution_count=$null;outputs=@();source=@("%%configure`n", "{`"numExecutors`": 1, `"conf`": {`"spark.dynamicAllocation.enabled`": `"false`"}}`n")},
        @{cell_type='markdown';metadata=@{};source=@("# Zava FinOps Demo Serving`n", "Manual versioned serving materialization. No existing tables, report, semantic model, schedules or Azure tags are changed.`n")},
        @{cell_type='code';metadata=@{};execution_count=$null;outputs=@();source=@(($code -split "`r?`n") | ForEach-Object { "$_`n" })}
    )
    foreach ($cell in $cells) {
        $cell.id = [guid]::NewGuid().ToString('N')
        $cell.metadata.id = $cell.id
        $cell.metadata.language = if ($cell.cell_type -eq 'markdown') { 'markdown' } else { 'python' }
    }
    $notebook = @{
        nbformat=4;nbformat_minor=5;cells=$cells
        metadata=@{
            kernelspec=@{display_name='Synapse PySpark';language='python';name='synapse_pyspark'}
            language_info=@{name='python'}
            dependencies=@{lakehouse=@{default_lakehouse=$deployment.lakehouseId;default_lakehouse_name=$deployment.lakehouseName;default_lakehouse_workspace_id=$deployment.workspaceId;known_lakehouses=@(@{id=$deployment.lakehouseId})}}
        }
    }
    $payload = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($notebook | ConvertTo-Json -Depth 30 -Compress)))
    return @{definition=@{format='ipynb';parts=@(@{path='notebook-content.ipynb';payloadType='InlineBase64';payload=$payload})}}
}

function New-FoundationNotebookDefinition {
    param(
        [Parameter(Mandatory)][string]$WorkspaceId,
        [Parameter(Mandatory)][string]$LakehouseId,
        [Parameter(Mandatory)][string]$BundleReceiptPath,
        [Parameter(Mandatory)][string]$AdapterPath
    )

    $receipt = Get-Content -LiteralPath $BundleReceiptPath -Raw | ConvertFrom-Json
    $adapterHash = (Get-FileHash -LiteralPath $AdapterPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $code = @"
import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
import notebookutils

context = notebookutils.runtime.context
if context['currentWorkspaceId'] != '$WorkspaceId' or context['defaultLakehouseId'] != '$LakehouseId':
    raise ValueError('Notebook is not bound to the approved isolated lakehouse')
base = Path('/lakehouse/default/Files')
adapter = base / 'foundation/input/run_foundation_fabric.py'
with adapter.open('rb') as source:
    if hashlib.file_digest(source, 'sha256').hexdigest() != '$adapterHash':
        raise ValueError('Unapproved Fabric adapter bytes')
exec(compile(adapter.read_text(encoding='utf-8'), str(adapter), 'exec'), globals())
relative_root = 'foundation/customers/zava-demo'
target = base / relative_root
try:
    validation = validate_and_stage(
        base / 'foundation/input/$($receipt.bundleFile)',
        '$($receipt.bundleSha256)',
        '$($receipt.coreSha256)',
        target,
    )
    result = materialize_candidates(spark, target, relative_root, validation)
    result.update(workspaceId='$WorkspaceId', lakehouseId='$LakehouseId', notebookId=context['currentNotebookId'], adapterSha256='$adapterHash')
    reports = target / 'reports'
    reports.mkdir(parents=True, exist_ok=True)
    (reports / f'{uuid.uuid4().hex}.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
except Exception as error:
    ledger = target / 'ledger'
    ledger.mkdir(parents=True, exist_ok=True)
    failure = dict(status='Quarantined', atUtc=datetime.now(timezone.utc).isoformat(), errorType=type(error).__name__, message=str(error))
    (ledger / f'{uuid.uuid4().hex}.json').write_text(json.dumps(failure, indent=2), encoding='utf-8')
    raise
print(json.dumps(result, indent=2))
display(spark.table(result['tables']['gold_monthly']))
display(spark.table(result['controlTable']))
"@

    $cells = @(
        @{cell_type='code';metadata=@{};execution_count=$null;outputs=@();source=@("%%configure`n", "{`"numExecutors`": 1, `"conf`": {`"spark.dynamicAllocation.enabled`": `"false`"}}`n")},
        @{cell_type='markdown';metadata=@{};source=@("# Zava FinOps Foundation Validation`n", "Manual candidate validation. No publication or live-report cutover. Financial batch is authoritative; operational usage and estimates are separate evidence.`n")},
        @{cell_type='code';metadata=@{};execution_count=$null;outputs=@();source=@(($code -split "`r?`n") | ForEach-Object { "$_`n" })}
    )
    foreach ($cell in $cells) {
        $cell.id = [guid]::NewGuid().ToString('N')
    }
    $notebook = @{
        nbformat=4
        nbformat_minor=5
        metadata=@{
            kernelspec=@{display_name='Synapse PySpark';language='python';name='synapse_pyspark'}
            language_info=@{name='python'}
            dependencies=@{lakehouse=@{default_lakehouse=$LakehouseId;default_lakehouse_name='ZavaFinOpsFoundation';default_lakehouse_workspace_id=$WorkspaceId;known_lakehouses=@(@{id=$LakehouseId})}}
        }
        cells=$cells
    }
    $payload = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($notebook | ConvertTo-Json -Depth 30 -Compress)))
    return @{definition=@{format='ipynb';parts=@(@{path='notebook-content.ipynb';payloadType='InlineBase64';payload=$payload})}}
}

function New-DatabricksMedallionNotebookDefinition {
    param(
        [Parameter(Mandatory)][string]$ProjectRoot,
        [Parameter(Mandatory)][object]$Batch,
        [Parameter(Mandatory)][object]$Deployment
    )

    $hashes = @{}
    foreach ($filename in @('databricks_medallion.py','finops_foundation.py','run_foundation_fabric.py')) {
        $hashes[$filename] = (Get-FileHash -LiteralPath (Join-Path $ProjectRoot "scripts/$filename") -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    $expectedHashes = $hashes | ConvertTo-Json -Compress
    $code = @"
import hashlib
import importlib.util
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
import notebookutils

context = notebookutils.runtime.context
if context['currentWorkspaceId'] != '$($Deployment.workspaceId)' or context['defaultLakehouseId'] != '$($Deployment.lakehouseId)':
    raise ValueError('Unexpected notebook lakehouse binding')
base = Path('/lakehouse/default/Files')
inputs = base / 'databricks/input/$($Batch.batchId)'
expected = json.loads('$expectedHashes')
for filename, expected_hash in expected.items():
    with (inputs / filename).open('rb') as source:
        if hashlib.file_digest(source, 'sha256').hexdigest() != expected_hash:
            raise ValueError('Unapproved ETL dependency bytes')
sys.path.insert(0, str(inputs))
specification = importlib.util.spec_from_file_location('databricks_operational_etl', inputs / 'databricks_medallion.py')
module = importlib.util.module_from_spec(specification)
specification.loader.exec_module(module)
destination = base / 'databricks/batches'
try:
    result = module.run_in_fabric(spark, inputs / 'databricks-$($Batch.batchId).zip', '$($Batch.bundleSha256)', destination)
    result.update(fabricWorkspaceId='$($Deployment.workspaceId)', lakehouseId='$($Deployment.lakehouseId)', notebookId=context['currentNotebookId'], pythonVersion=sys.version.split()[0], pyarrowVersion=module.arrow.__version__)
    reports = destination / '$($Batch.batchId)' / 'reports'
    reports.mkdir(parents=True, exist_ok=True)
    (reports / f'{uuid.uuid4().hex}.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
except Exception as error:
    quarantine = destination / 'quarantine'
    quarantine.mkdir(parents=True, exist_ok=True)
    (quarantine / f'{uuid.uuid4().hex}.json').write_text(json.dumps(dict(status='Quarantined', atUtc=datetime.now(timezone.utc).isoformat(), message=str(error), batchId='$($Batch.batchId)')), encoding='utf-8')
    raise
print(json.dumps(result, indent=2))
display(spark.table(result['tables']['gold_usage_daily']))
display(spark.table(result['tables']['gold_queries_daily']))
"@
    $cells = @(
        @{cell_type='code';metadata=@{};execution_count=$null;outputs=@();source=@("%%configure`n", "{`"numExecutors`": 1, `"conf`": {`"spark.dynamicAllocation.enabled`": `"false`"}}`n")},
        @{cell_type='markdown';metadata=@{};source=@("# Databricks Operational Medallion`n", "Manual May-June 2026 snapshot. DBUs and query metrics explain usage; they are not additional Azure billed charges. No job-run allocation or per-query cost is inferred.`n")},
        @{cell_type='code';metadata=@{};execution_count=$null;outputs=@();source=@(($code -split "`r?`n") | ForEach-Object { "$_`n" })}
    )
    foreach ($cell in $cells) { $cell.id = [guid]::NewGuid().ToString('N') }
    $notebook = @{
        nbformat=4;nbformat_minor=5;cells=$cells
        metadata=@{
            kernelspec=@{display_name='Synapse PySpark';language='python';name='synapse_pyspark'}
            language_info=@{name='python'}
            dependencies=@{lakehouse=@{default_lakehouse=$Deployment.lakehouseId;default_lakehouse_name=$Deployment.lakehouseName;default_lakehouse_workspace_id=$Deployment.workspaceId;known_lakehouses=@(@{id=$Deployment.lakehouseId})}}
        }
    }
    $payload = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($notebook | ConvertTo-Json -Depth 30 -Compress)))
    return @{definition=@{format='ipynb';parts=@(@{path='notebook-content.ipynb';payloadType='InlineBase64';payload=$payload})}}
}