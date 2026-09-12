[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Workspace
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Assert-NoReparseDescendants {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }
    $items = @((Get-Item -LiteralPath $Path -Force))
    if (Test-Path -LiteralPath $Path -PathType Container) {
        $items += @(Get-ChildItem -LiteralPath $Path -Recurse -Force)
    }
    $reparsePoint = $items |
        Where-Object {
            ($_.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0
        } |
        Select-Object -First 1
    if ($null -ne $reparsePoint) {
        throw "Refusing to validate through a reparse point: $($reparsePoint.FullName)"
    }
}

if (-not (Test-Path -LiteralPath $Workspace -PathType Container)) {
    throw "Workspace directory does not exist: $Workspace"
}

$workspaceRoot = (Resolve-Path -LiteralPath $Workspace).Path
$coordinationRoot = Join-Path $workspaceRoot '.agent-coordination'
Assert-NoReparseDescendants -Path $coordinationRoot
$requiredFiles = @(
    (Join-Path $coordinationRoot 'README.md'),
    (Join-Path $coordinationRoot 'status.md'),
    (Join-Path $coordinationRoot 'handoff-contract.md'),
    (Join-Path $coordinationRoot 'observer\run.py'),
    (Join-Path $coordinationRoot 'observer\VERSION'),
    (Join-Path $coordinationRoot 'observer\static\index.html'),
    (Join-Path $coordinationRoot 'observer\static\app.css'),
    (Join-Path $coordinationRoot 'observer\static\app.js'),
    (Join-Path $coordinationRoot 'observer\observer\engine.py')
)

$missing = @($requiredFiles | Where-Object { -not (Test-Path -LiteralPath $_ -PathType Leaf) })
if ($missing.Count -gt 0) {
    throw "Coordination workspace is incomplete. Missing: $($missing -join ', ')"
}

$pythonCommand = Get-Command python -ErrorAction SilentlyContinue | Select-Object -First 1
if ($null -eq $pythonCommand) {
    throw 'Python is required to validate the observer.'
}

$runScript = Join-Path $coordinationRoot 'observer\run.py'
$snapshotLines = & $pythonCommand.Source $runScript --workspace $workspaceRoot --once
if ($LASTEXITCODE -ne 0) {
    throw "Observer snapshot failed with exit code $LASTEXITCODE"
}

$snapshotText = $snapshotLines -join [Environment]::NewLine
$snapshot = $snapshotText | ConvertFrom-Json
if ([string]$snapshot.schema_version -ne '1') {
    throw "Unexpected observer schema version: $($snapshot.schema_version)"
}
if ([System.IO.Path]::GetFullPath([string]$snapshot.workspace) -ne $workspaceRoot) {
    throw "Observer reported a different workspace: $($snapshot.workspace)"
}

Write-Output "workspace: $workspaceRoot"
Write-Output "schema_version: $($snapshot.schema_version)"
Write-Output 'validation: ok'
