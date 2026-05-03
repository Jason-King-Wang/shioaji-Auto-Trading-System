param(
    [Parameter(Mandatory = $true)]
    [string]$RunDate,
    [Parameter(Mandatory = $true)]
    [string]$AtTime,
    [string]$UntilTime = "13:20",
    [string]$ProjectRoot = "",
    [string]$RunnerScript = "",
    [string]$PythonPath = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not $ProjectRoot) {
    $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    $ProjectRoot = Split-Path -Parent $scriptDir
}
if (-not (Test-Path -LiteralPath $ProjectRoot)) {
    throw "ProjectRoot was not found. Scheduled task was not registered."
}

if (-not $RunnerScript) {
    $RunnerScript = Join-Path $ProjectRoot "scripts\run_allowed_2330_live_order_task.ps1"
}
if (-not (Test-Path -LiteralPath $RunnerScript)) {
    throw "RunnerScript was not found. Scheduled task was not registered."
}

if ($PythonPath -and (Test-Path -LiteralPath $PythonPath)) {
    $python = $PythonPath
} else {
    $python = (Get-Command python.exe -ErrorAction Stop).Source
}

$scheduledAt = [datetime]::ParseExact(("{0} {1}" -f $RunDate, $AtTime), "yyyy-MM-dd HH:mm", $null)
$scheduledUntil = [datetime]::ParseExact(("{0} {1}" -f $RunDate, $UntilTime), "yyyy-MM-dd HH:mm", $null)
if ($scheduledUntil -le $scheduledAt) {
    throw "UntilTime must be after AtTime. Scheduled task was not registered."
}
$now = Get-Date
if ($scheduledUntil -le $now) {
    throw "RunDate/AtTime is in the past and the catch-up window is closed. Scheduled task was not registered."
}
if ($scheduledAt -le $now) {
    Write-Host ("RunDate/AtTime already passed, but catch-up window remains open until {0}." -f $scheduledUntil.ToString("s"))
}

Write-Host "Running runner smoke test before registering task..."
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $RunnerScript -PythonPath $python -SmokeTest
if ($LASTEXITCODE -ne 0) {
    throw "Runner smoke test failed. Scheduled task was not registered."
}

Write-Host "Running live guard preflight before registering task..."
$preflightCode = @'
import sys
from pathlib import Path

project_root = Path(sys.argv[1])
sys.path.insert(0, str(project_root / 'src'))

from sinopac_auto_trading.config import (
    Settings,
    describe_live_submit_guard,
    ensure_auto_trading_live_enabled,
)

changed, config_path = ensure_auto_trading_live_enabled(project_root / 'config')
settings = Settings.load(project_root)
allowed, reason = settings.evaluate_live_submit_guard(confirm_live=True)
print(f'live_enabled_config_path={config_path}')
print(f'live_enabled_auto_enabled={str(changed).lower()}')
print(f'live_guard={reason}')
if not allowed:
    raise SystemExit(describe_live_submit_guard(reason))
'@

$previousAutoTradeLive = $env:AUTO_TRADE_LIVE
try {
    $env:AUTO_TRADE_LIVE = "1"
    & $python -c $preflightCode $ProjectRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Live guard preflight failed. Scheduled task was not registered."
    }
} finally {
    if ($null -eq $previousAutoTradeLive) {
        Remove-Item Env:\AUTO_TRADE_LIVE -ErrorAction SilentlyContinue
    } else {
        $env:AUTO_TRADE_LIVE = $previousAutoTradeLive
    }
}

Write-Host "live_order_schedule_preflight: ok"
