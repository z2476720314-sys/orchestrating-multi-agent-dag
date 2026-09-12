[CmdletBinding()]
param(
    [switch]$NoBrowser,
    [ValidateRange(1, 65535)]
    [int]$Port = 8767
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$scriptRoot = $PSScriptRoot
$workspace = (Resolve-Path -LiteralPath (Join-Path $scriptRoot '..\..')).Path
$runPath = Join-Path $scriptRoot 'run.py'
$hostAddress = '127.0.0.1'
$pageUrl = "http://${hostAddress}:$Port/"
$browserJob = $null

function Find-RunnablePython {
    $candidates = @(Get-Command python -All -CommandType Application -ErrorAction SilentlyContinue)
    foreach ($candidate in $candidates) {
        $candidatePath = $candidate.Source
        if ([string]::IsNullOrWhiteSpace($candidatePath)) {
            continue
        }

        try {
            & $candidatePath -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' *> $null
            if ($LASTEXITCODE -eq 0) {
                return $candidatePath
            }
        }
        catch {
            continue
        }
    }

    throw 'No runnable Python 3.11 or newer was found on PATH.'
}

$pythonPath = Find-RunnablePython

if (-not $NoBrowser) {
    $browserJob = Start-Job -ScriptBlock {
        param($HealthUrl, $PageUrl)

        for ($attempt = 0; $attempt -lt 50; $attempt++) {
            try {
                $response = Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec 1
                if ($response.StatusCode -eq 200) {
                    Start-Process -FilePath $PageUrl
                    return
                }
            }
            catch {
                Start-Sleep -Milliseconds 100
            }
        }
    } -ArgumentList "${pageUrl}api/health", $pageUrl
}

try {
    & $pythonPath $runPath `
        --workspace $workspace `
        --host $hostAddress `
        --port $Port
    exit $LASTEXITCODE
}
finally {
    if ($null -ne $browserJob) {
        Stop-Job -Job $browserJob -ErrorAction SilentlyContinue
        Remove-Job -Job $browserJob -Force -ErrorAction SilentlyContinue
    }
}
