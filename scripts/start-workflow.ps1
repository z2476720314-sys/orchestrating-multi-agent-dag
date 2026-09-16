[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Workspace,
    [ValidateRange(1, 65535)]
    [int]$Port = 8767,
    [switch]$NoBrowser
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Find-RunnablePython {
    foreach ($candidate in @(Get-Command python -All -CommandType Application -ErrorAction SilentlyContinue)) {
        try {
            & $candidate.Source -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' *> $null
            if ($LASTEXITCODE -eq 0) { return $candidate.Source }
        }
        catch { continue }
    }
    throw 'No runnable Python 3.11 or newer was found on PATH.'
}

function Test-ObserverHealth {
    param([string]$Url, [string]$ExpectedWorkspace)
    try {
        $health = Invoke-WebRequest -Uri "${Url}api/health" -UseBasicParsing -TimeoutSec 1
        if ($health.StatusCode -ne 200) { return $false }
        $snapshot = Invoke-RestMethod -Uri "${Url}api/snapshot" -TimeoutSec 1
        return [System.IO.Path]::GetFullPath([string]$snapshot.workspace) -eq $ExpectedWorkspace
    }
    catch { return $false }
}

$workspaceRoot = (Resolve-Path -LiteralPath $Workspace).Path
if ($workspaceRoot -eq [System.IO.Path]::GetPathRoot($workspaceRoot)) {
    throw "Refusing to start from a filesystem root: $workspaceRoot"
}
$initializer = Join-Path $PSScriptRoot 'init-coordination.ps1'
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $initializer -Workspace $workspaceRoot -RefreshObserver *> $null
if ($LASTEXITCODE -ne 0) { throw "Coordination initialization failed with exit code $LASTEXITCODE" }

$runtimeRoot = Join-Path $workspaceRoot '.agent-coordination\runtime'
New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
$statePath = Join-Path $runtimeRoot 'observer.json'
$pageUrl = "http://127.0.0.1:$Port/"
$reused = $false

if (Test-Path -LiteralPath $statePath -PathType Leaf) {
    try {
        $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
        $samePort = [int]$state.port -eq $Port
        $process = Get-Process -Id ([int]$state.pid) -ErrorAction SilentlyContinue
        if ($samePort -and $null -ne $process -and (Test-ObserverHealth -Url $pageUrl -ExpectedWorkspace $workspaceRoot)) {
            $reused = $true
            $observerPid = [int]$state.pid
        }
    }
    catch { $reused = $false }
}

if (-not $reused) {
    if (Test-ObserverHealth -Url $pageUrl -ExpectedWorkspace $workspaceRoot) {
        throw "Observer is healthy on $pageUrl but is not owned by this workflow state file."
    }
    $pythonPath = Find-RunnablePython
    $runPath = Join-Path $workspaceRoot '.agent-coordination\observer\run.py'
    $arguments = @(
        ('"{0}"' -f $runPath),
        '--workspace', ('"{0}"' -f $workspaceRoot),
        '--host', '127.0.0.1',
        '--port', [string]$Port
    )
    $process = Start-Process -FilePath $pythonPath -ArgumentList $arguments -WindowStyle Hidden -PassThru
    $observerPid = $process.Id
    $healthy = $false
    for ($attempt = 0; $attempt -lt 100; $attempt++) {
        if ($process.HasExited) { break }
        if (Test-ObserverHealth -Url $pageUrl -ExpectedWorkspace $workspaceRoot) {
            $healthy = $true
            break
        }
        Start-Sleep -Milliseconds 100
    }
    if (-not $healthy) {
        Stop-Process -Id $observerPid -Force -ErrorAction SilentlyContinue
        throw "Observer did not become healthy on $pageUrl"
    }
    [ordered]@{
        pid = $observerPid
        port = $Port
        url = $pageUrl
        workspace = $workspaceRoot
        started_at = [DateTimeOffset]::UtcNow.ToString('o')
    } | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding UTF8
}

if (-not $NoBrowser) {
    Start-Process -FilePath $pageUrl | Out-Null
}

Write-Output "url: $pageUrl"
Write-Output "pid: $observerPid"
Write-Output "reused: $($reused.ToString().ToLowerInvariant())"
