<#
.SYNOPSIS
    Scoped, Merge-only Azure Resource Manager (ARM) tag governance helper and test harness.
.DESCRIPTION
    Validates proposed resource tag changes against a strict single-resource scope allowlist,
    6-key tag template, and Merge-only semantics (blocking tag deletions and preserving unrelated
    existing tags). Verifies caller Entra OID and Tenant ID before executing an ARM PATCH Merge
    and subsequent GET readback verification.
#>

function Resolve-AzureCliPath {
    if ($env:AZURE_CLI_PATH -and (Test-Path -LiteralPath $env:AZURE_CLI_PATH)) {
        return $env:AZURE_CLI_PATH
    }
    $cmd = Get-Command 'az.cmd', 'az' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($cmd) { return $cmd.Source }
    $defaultWin = Join-Path $env:ProgramFiles 'Microsoft SDKs\Azure\CLI2\wbin\az.cmd'
    if (Test-Path -LiteralPath $defaultWin) { return $defaultWin }
    throw "Azure CLI ('az') was not found on PATH. Install Azure CLI or set AZURE_CLI_PATH."
}

function Get-NativeTagHash {
    param([Parameter(Mandatory)][object]$Value)
    $bytes = [Text.Encoding]::UTF8.GetBytes(($Value | ConvertTo-Json -Depth 20 -Compress))
    return [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($bytes)).ToLowerInvariant()
}

function New-NativeTagPlan {
    param(
        [Parameter(Mandatory)][object]$Configuration,
        [Parameter(Mandatory)][System.Collections.IDictionary]$CurrentTags
    )
    $expectedScope = "/subscriptions/$($Configuration.subscriptionId)/resourceGroups/$($Configuration.approvedResourceGroup)/providers/Microsoft.Fabric/capacities/astoriafabricnorthcentralus"
    if ($Configuration.proposedResourceId -ine $expectedScope -or $Configuration.operation -ne 'Merge' -or $Configuration.allowTagDeletion -ne $false -or $Configuration.preserveUnrelatedTags -ne $true) {
        throw 'Tag proposal exceeds the approved single-resource merge scope'
    }
    $allowed = @('environment','department','project','cost-center','workload','owner')
    $before = [ordered]@{}
    foreach ($key in @($CurrentTags.Keys) | Sort-Object) { $before[$key] = [string]$CurrentTags[$key] }
    $requested = [ordered]@{}
    foreach ($property in $Configuration.proposedTags.PSObject.Properties | Sort-Object Name) {
        if ($property.Name -cnotin $allowed -or $property.Value -isnot [string] -or [string]::IsNullOrWhiteSpace($property.Value) -or $property.Value.Length -gt 256) {
            throw 'Unapproved tag key or invalid value'
        }
        $requested[$property.Name] = $property.Value
    }
    if ($requested.Count -ne $allowed.Count -or $requested['environment'] -cne 'demo') { throw 'Expected the six reviewed demo classification tags' }
    $after = [ordered]@{}
    foreach ($key in $before.Keys) { $after[$key] = $before[$key] }
    foreach ($key in $requested.Keys) {
        $existingKey = @($after.Keys | Where-Object { $_ -ieq $key })
        if ($existingKey.Count) { $after[$existingKey[0]] = $requested[$key] } else { $after[$key] = $requested[$key] }
    }
    if ($after.Count -gt 50) { throw 'Azure tag count limit would be exceeded' }
    $intent = [ordered]@{resourceId=$Configuration.proposedResourceId;before=$before;requested=$requested;operation='Merge'}
    return [pscustomobject]@{resourceId=$Configuration.proposedResourceId;before=$before;requested=$requested;after=$after;previewHash=(Get-NativeTagHash $intent);operation='Merge'}
}

function Write-NativeTagReceipt {
    param([Parameter(Mandatory)][string]$Directory,[Parameter(Mandatory)][object]$Receipt)
    [IO.Directory]::CreateDirectory($Directory) | Out-Null
    $path = Join-Path $Directory "$([guid]::NewGuid().ToString('N')).json"
    $stream = [IO.File]::Open($path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
    try {
        $payload = [Text.Encoding]::UTF8.GetBytes(($Receipt | ConvertTo-Json -Depth 20))
        $stream.Write($payload, 0, $payload.Length)
    } finally { $stream.Dispose() }
}

function Invoke-NativeTagDemo {
    param(
        [Parameter(Mandatory)][string]$ConfigurationPath,
        [switch]$Apply,
        [string]$ExpectedPreviewHash,
        [string]$Reason,
        [string]$ReceiptDirectory
    )
    $configuration = Get-Content -LiteralPath $ConfigurationPath -Raw | ConvertFrom-Json -ErrorAction Stop
    $azCli = Resolve-AzureCliPath
    $token = & $azCli account get-access-token --subscription $configuration.subscriptionId --resource https://management.azure.com/ --query accessToken -o tsv
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($token)) { throw 'ARM authentication failed' }
    $tokenParts = $token.Trim().Split('.')
    if ($tokenParts.Count -lt 2) { throw 'Malformed ARM JWT access token' }
    $encodedClaims = $tokenParts[1].Replace('-','+').Replace('_','/')
    $encodedClaims = $encodedClaims.PadRight($encodedClaims.Length + ((4 - $encodedClaims.Length % 4) % 4), '=')
    $claims = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($encodedClaims)) | ConvertFrom-Json
    if ($claims.oid -ine $configuration.demoActorObjectId -or $claims.tid -ine $configuration.tenantId) { throw 'The CLI session is not the approved demo admin and tenant' }
    $headers = @{Authorization="Bearer $token"}
    $uri = "https://management.azure.com$($configuration.proposedResourceId)/providers/Microsoft.Resources/tags/default?api-version=2021-04-01"
    $current = Invoke-RestMethod -Uri $uri -Headers $headers -ErrorAction Stop
    $currentTags = if ($null -eq $current.properties.tags) { @{} } else { $current.properties.tags | ConvertTo-Json -Depth 10 | ConvertFrom-Json -AsHashtable }
    $plan = New-NativeTagPlan -Configuration $configuration -CurrentTags $currentTags
    if (-not $Apply) { return $plan }
    if ($configuration.manualAdminTestApproved -ne $true -or $plan.previewHash -cne $ExpectedPreviewHash -or [string]::IsNullOrWhiteSpace($ReceiptDirectory) -or $Reason.Trim().Length -lt 10) {
        throw 'Explicit approved test, unchanged preview hash, audit directory and meaningful reason are required'
    }
    $requestId = [guid]::NewGuid().ToString()
    $headers['x-ms-client-request-id'] = $requestId
    $receipt = [ordered]@{status='Requested';atUtc=[DateTime]::UtcNow.ToString('o');requestId=$requestId;actorObjectId=$claims.oid;tenantId=$claims.tid;resourceId=$plan.resourceId;before=$plan.before;requested=$plan.requested;reason=$Reason;previewHash=$plan.previewHash;executionPath='Admin Azure CLI token; not Power Automate or report invocation'}
    Write-NativeTagReceipt -Directory $ReceiptDirectory -Receipt $receipt
    try {
        $response = Invoke-WebRequest -Method Patch -Uri $uri -Headers $headers -ContentType 'application/json' -Body (@{operation='Merge';properties=@{tags=$plan.requested}} | ConvertTo-Json -Depth 10) -ErrorAction Stop
        $readback = Invoke-RestMethod -Uri $uri -Headers $headers -ErrorAction Stop
        $observed = $readback.properties.tags | ConvertTo-Json -Depth 10 | ConvertFrom-Json -AsHashtable
        foreach ($key in $plan.after.Keys) { if (-not $observed.ContainsKey($key) -or $observed[$key] -cne $plan.after[$key]) { throw 'Tag readback does not match approved changes or retained tags' } }
        $receipt.status = 'Verified'
        $receipt['after'] = $observed
        $receipt['azureRequestId'] = [string]($response.Headers['x-ms-request-id'] | Select-Object -First 1)
        $receipt['httpStatus'] = [int]$response.StatusCode
        $receipt.atUtc = [DateTime]::UtcNow.ToString('o')
        Write-NativeTagReceipt -Directory $ReceiptDirectory -Receipt $receipt
        return [pscustomobject]$receipt
    } catch {
        $receipt.status = 'FailedOrUnverified'
        $receipt['message'] = $_.Exception.Message
        Write-NativeTagReceipt -Directory $ReceiptDirectory -Receipt $receipt
        throw
    }
}