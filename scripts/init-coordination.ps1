[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Workspace,

    [switch]$RefreshObserver
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Resolve-SafeWorkspace {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        throw "Workspace directory does not exist: $Path"
    }

    $resolved = (Resolve-Path -LiteralPath $Path).Path
    $root = [System.IO.Path]::GetPathRoot($resolved)
    if ([string]::IsNullOrWhiteSpace($resolved) -or $resolved -eq $root) {
        throw "Refusing to initialize a filesystem root: $resolved"
    }
    return $resolved
}

function Assert-NotReparsePoint {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }
    $item = Get-Item -LiteralPath $Path -Force
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Refusing to write through a reparse point: $Path"
    }
}

function Assert-NoReparseDescendants {
    param([Parameter(Mandatory = $true)][string]$Path)

    Assert-NotReparsePoint -Path $Path
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        return
    }
    $reparsePoint = Get-ChildItem -LiteralPath $Path -Recurse -Force |
        Where-Object {
            ($_.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0
        } |
        Select-Object -First 1
    if ($null -ne $reparsePoint) {
        throw "Refusing to write through a reparse point: $($reparsePoint.FullName)"
    }
}

function Write-Utf8FileIfMissing {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Content,
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][System.Collections.Generic.List[string]]$Created,
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][System.Collections.Generic.List[string]]$Skipped
    )

    if (Test-Path -LiteralPath $Path) {
        $Skipped.Add($Path)
        return
    }

    $parent = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    Set-Content -LiteralPath $Path -Value $Content -Encoding UTF8
    $Created.Add($Path)
}

function Get-Sha256Hex {
    param([Parameter(Mandatory = $true)][string]$Path)

    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    $stream = [System.IO.File]::OpenRead($Path)
    try {
        $hash = $algorithm.ComputeHash($stream)
        return ([System.BitConverter]::ToString($hash)).Replace('-', '')
    }
    finally {
        $stream.Dispose()
        $algorithm.Dispose()
    }
}

function Copy-ObserverTemplate {
    param(
        [Parameter(Mandatory = $true)][string]$Template,
        [Parameter(Mandatory = $true)][string]$Destination,
        [Parameter(Mandatory = $true)][bool]$Refresh,
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][System.Collections.Generic.List[string]]$Created,
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][System.Collections.Generic.List[string]]$Skipped,
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][System.Collections.Generic.List[string]]$Refreshed
    )

    if (Test-Path -LiteralPath $Destination) {
        Assert-NotReparsePoint -Path $Destination
        if (-not $Refresh) {
            $Skipped.Add($Destination)
            return
        }
        Assert-NoReparseDescendants -Path $Destination
    }

    if (-not (Test-Path -LiteralPath $Destination)) {
        New-Item -ItemType Directory -Path $Destination -Force | Out-Null
        $Created.Add($Destination)
    }

    $excludedDirectories = @(
        'artifacts',
        '__pycache__',
        '.mypy_cache',
        '.ruff_cache',
        '.pytest_cache',
        'node_modules'
    )
    $sourceFiles = Get-ChildItem -LiteralPath $Template -Recurse -File | Where-Object {
        $candidateRelative = $_.FullName.Substring($Template.Length + 1)
        $candidateParts = $candidateRelative -split '[\\/]'
        $hasExcludedDirectory = @(
            $candidateParts | Where-Object { $excludedDirectories -contains $_ }
        ).Count -gt 0
        $hasExcludedDirectory -eq $false -and
            $_.Extension -notin @('.pyc', '.pyo', '.log') -and
            $_.Name -notmatch '\.bak(?:-|$)' -and
            $_.Name -ne '.coverage'
    }

    foreach ($sourceFile in $sourceFiles) {
        $relative = $sourceFile.FullName.Substring($Template.Length + 1)
        $targetFile = Join-Path $Destination $relative
        $targetParent = Split-Path -Parent $targetFile
        if (-not (Test-Path -LiteralPath $targetParent)) {
            New-Item -ItemType Directory -Path $targetParent -Force | Out-Null
        }

        if (-not (Test-Path -LiteralPath $targetFile)) {
            Copy-Item -LiteralPath $sourceFile.FullName -Destination $targetFile
            $Created.Add($targetFile)
            continue
        }

        if (-not $Refresh) {
            $Skipped.Add($targetFile)
            continue
        }

        $sourceHash = Get-Sha256Hex -Path $sourceFile.FullName
        $targetHash = Get-Sha256Hex -Path $targetFile
        if ($sourceHash -eq $targetHash) {
            $Skipped.Add($targetFile)
            continue
        }

        $codeExtensions = @('.py', '.js', '.css', '.ps1', '.ts', '.tsx', '.vue')
        $backupFile = $null
        $extension = [System.IO.Path]::GetExtension($targetFile).ToLowerInvariant()
        if ($codeExtensions -contains $extension) {
            $timestamp = Get-Date -Format 'yyyyMMdd-HHmmssfff'
            $uniqueSuffix = [guid]::NewGuid().ToString('N')
            $backupFile = "$targetFile.bak-$timestamp-$uniqueSuffix"
            Copy-Item -LiteralPath $targetFile -Destination $backupFile
        }
        Copy-Item -LiteralPath $sourceFile.FullName -Destination $targetFile -Force
        if ($null -ne $backupFile) {
            $Refreshed.Add("$targetFile (backup: $backupFile)")
        }
        else {
            $Refreshed.Add($targetFile)
        }
    }
}

$workspaceRoot = Resolve-SafeWorkspace -Path $Workspace
$skillRoot = Split-Path -Parent $PSScriptRoot
$observerTemplate = Join-Path $skillRoot 'assets\observer-template'
if (-not (Test-Path -LiteralPath $observerTemplate -PathType Container)) {
    throw "Observer template is missing: $observerTemplate"
}

$coordinationRoot = Join-Path $workspaceRoot '.agent-coordination'
$handoffRoot = Join-Path $coordinationRoot 'handoffs'
$observerRoot = Join-Path $coordinationRoot 'observer'

$created = [System.Collections.Generic.List[string]]::new()
$skipped = [System.Collections.Generic.List[string]]::new()
$refreshed = [System.Collections.Generic.List[string]]::new()

foreach ($directory in @($coordinationRoot, $handoffRoot)) {
    if (Test-Path -LiteralPath $directory) {
        Assert-NotReparsePoint -Path $directory
        $skipped.Add($directory)
    }
    else {
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
        $created.Add($directory)
    }
}

$coordinationReadme = @"
# Multi-Agent Shared Workspace

Workspace: ``$workspaceRoot``

Codex coordinates and integrates. DSH performs investigation, validation, and read-only review unless a task card grants an exact write scope. WorkBuddy performs bounded implementation, testing, and real-entry acceptance when its task card grants write access.

Before work begins, read this file and ``status.md`` and inspect ``git status --short``. Treat every existing change as user-owned. A task card must declare task ID, objective, owner, read scope, write scope, prohibitions, success criteria, dependencies, and output. One file has only one writer at a time.

Each completed task writes a self-contained handoff under ``handoffs/``. Report only evidence-backed status. Never invent progress percentages, ETA, success, model selection, or validation results.

## Default routing

- DSH: ``workbuddy/deepseek-v4.1-flash`` with effort ``max``. Headless reads the shared ``agent-default-model``; verify it before dispatch and verify the session projection afterward.
- WorkBuddy: ``glm-5.3-flash`` with effort ``high``. Pass ``--model`` and ``--effort`` on every ``codebuddy`` invocation.

## Observer

Run ``.\observer\start-observer.ps1`` from this directory, or run ``python .\observer\run.py --workspace '$workspaceRoot' --once`` for a machine-readable snapshot.
"@

$taskSectionName = -join ([char[]](0x5F53, 0x524D, 0x4EFB, 0x52A1))
$statusTemplate = @"
# Multi-Agent Coordination Status

Update this file only from verifiable process, event, handoff, command, test, or real-entry evidence. ``accepted`` or process exit alone is not completion.

| Agent | Status | Default role | Current write scope |
|---|---|---|---|
| Codex | unknown | Coordination, integration, final verification | None |
| DSH | unknown | Investigation, validation, read-only review | None |
| WorkBuddy | unknown | Bounded implementation, tests, real-entry acceptance | None |

## $taskSectionName

| Task ID | Owner | Status | Read scope | Write scope | Success evidence | Blocker |
|---|---|---|---|---|---|---|
"@

$handoffReadme = @"
# Handoff Contract

Create one Markdown file per task. Include: task ID, owner, terminal or blocked state, exact files changed, commands run with exit codes, relevant output, real-entry evidence, risks, unresolved items, and the next safe action. Do not include credentials, cookies, hidden prompts, private reasoning, or unredacted tool payloads.
"@

Write-Utf8FileIfMissing -Path (Join-Path $coordinationRoot 'README.md') -Content $coordinationReadme -Created $created -Skipped $skipped
Write-Utf8FileIfMissing -Path (Join-Path $coordinationRoot 'status.md') -Content $statusTemplate -Created $created -Skipped $skipped
Write-Utf8FileIfMissing -Path (Join-Path $coordinationRoot 'handoff-contract.md') -Content $handoffReadme -Created $created -Skipped $skipped
Copy-ObserverTemplate -Template $observerTemplate -Destination $observerRoot -Refresh ([bool]$RefreshObserver) -Created $created -Skipped $skipped -Refreshed $refreshed

Write-Output "workspace: $workspaceRoot"
Write-Output "created: $($created.Count)"
foreach ($item in $created) { Write-Output "  + $item" }
Write-Output "skipped: $($skipped.Count)"
foreach ($item in $skipped) { Write-Output "  = $item" }
Write-Output "refreshed: $($refreshed.Count)"
foreach ($item in $refreshed) { Write-Output "  * $item" }
