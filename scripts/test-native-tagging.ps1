$project = Split-Path $PSScriptRoot -Parent
. (Join-Path $project 'business-context/native-tagging.ps1')
$configuration = Get-Content (Join-Path $project 'config/native-tagging-demo.json') -Raw | ConvertFrom-Json
$plan = New-NativeTagPlan -Configuration $configuration -CurrentTags @{RetainMe='unchanged';Department='Old'}
if ($plan.after.RetainMe -cne 'unchanged' -or $plan.after.Department -cne 'Finance' -or $plan.after.Count -ne 7) { throw 'Merge preservation or case-insensitive key handling failed' }
$replay = New-NativeTagPlan -Configuration $configuration -CurrentTags @{Department='Old';RetainMe='unchanged'}
if ($replay.previewHash -cne $plan.previewHash) { throw 'Preview hash is not deterministic' }
$changed = New-NativeTagPlan -Configuration $configuration -CurrentTags @{Department='Changed';RetainMe='unchanged'}
if ($changed.previewHash -ceq $plan.previewHash) { throw 'Stale preview change not detected' }
$configuration.operation = 'Delete'
$rejected = $false
try { New-NativeTagPlan -Configuration $configuration -CurrentTags @{} | Out-Null } catch { $rejected = $true }
if (-not $rejected) { throw 'Delete operation was accepted' }
$configuration.operation = 'Merge'
$configuration.proposedResourceId = $configuration.proposedResourceId.Replace('RG-fabric','another-group')
$rejected = $false
try { New-NativeTagPlan -Configuration $configuration -CurrentTags @{} | Out-Null } catch { $rejected = $true }
if (-not $rejected) { throw 'Out-of-scope target was accepted' }
Write-Output 'PASS: tag preservation, case handling, deterministic/stale preview, delete denial and scope denial; no network calls.'