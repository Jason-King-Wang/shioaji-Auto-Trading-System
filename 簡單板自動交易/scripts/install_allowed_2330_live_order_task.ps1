param(
    [string]$TaskName = "SinoPac2330BuyIntradayOdd0910",
    [Parameter(Mandatory = $true)]
    [string]$RunDate,
    [string]$AtTime = "09:10",
    [string]$UntilTime = "13:20",
    [int]$RetryIntervalMinutes = 5,
    [string]$ProjectRoot = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$resolvedProjectRoot = if ($ProjectRoot) { $ProjectRoot } else { Split-Path -Parent $scriptDir }
$runnerScript = Join-Path $scriptDir "run_allowed_2330_live_order_task.ps1"
$preflightScript = Join-Path $scriptDir "assert_live_order_schedule_preflight.ps1"
$pythonPath = (Get-Command python.exe -ErrorAction Stop).Source
$userId = if ($env:USERDOMAIN) {
    "{0}\{1}" -f $env:USERDOMAIN, $env:USERNAME
} else {
    $env:USERNAME
}

$scheduledAt = [datetime]::ParseExact(("{0} {1}" -f $RunDate, $AtTime), "yyyy-MM-dd HH:mm", $null)
$scheduledUntil = [datetime]::ParseExact(("{0} {1}" -f $RunDate, $UntilTime), "yyyy-MM-dd HH:mm", $null)
$now = Get-Date
$effectiveTriggerAt = if ($scheduledAt -le $now) { $now.AddMinutes(1) } else { $scheduledAt }
$repetitionDuration = $scheduledUntil - $effectiveTriggerAt
if ($RetryIntervalMinutes -lt 1) {
    throw "RetryIntervalMinutes must be at least 1. Scheduled task was not registered."
}
if ($repetitionDuration.TotalSeconds -le 0) {
    throw "The catch-up window is closed. Scheduled task was not registered."
}
$actionArgument = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -PythonPath "{1}"' -f $runnerScript, $pythonPath
$description = "Run the only allowed SinoPac live order automation on {0}: 2330 Buy IntradayOdd 1 share from {1} to {2} with price cap 2100, retry every {3} minutes, with duplicate-order guard." -f $RunDate, $AtTime, $UntilTime, $RetryIntervalMinutes

& powershell.exe `
    -NoProfile `
    -ExecutionPolicy Bypass `
    -File $preflightScript `
    -RunDate $RunDate `
    -AtTime $AtTime `
    -UntilTime $UntilTime `
    -ProjectRoot $resolvedProjectRoot `
    -RunnerScript $runnerScript `
    -PythonPath $pythonPath
if ($LASTEXITCODE -ne 0) {
    throw "Live order schedule preflight failed. Scheduled task was not registered."
}

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $actionArgument
$trigger = New-ScheduledTaskTrigger `
    -Once `
    -At $effectiveTriggerAt `
    -RepetitionInterval (New-TimeSpan -Minutes $RetryIntervalMinutes) `
    -RepetitionDuration $repetitionDuration
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 5)
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description $description `
    -Force | Out-Null

$task = Get-ScheduledTask -TaskName $TaskName
$taskInfo = Get-ScheduledTaskInfo -TaskName $TaskName

Write-Host ("Scheduled task '{0}' created." -f $TaskName)
Write-Host ("RunDate: {0}" -f $RunDate)
Write-Host ("AtTime: {0}" -f $AtTime)
Write-Host ("UntilTime: {0}" -f $UntilTime)
Write-Host ("EffectiveFirstRun: {0}" -f $effectiveTriggerAt)
Write-Host ("RetryIntervalMinutes: {0}" -f $RetryIntervalMinutes)
Write-Host ("ProjectRoot: {0}" -f $resolvedProjectRoot)
Write-Host ("Runner: {0}" -f $runnerScript)
Write-Host ("Python: {0}" -f $pythonPath)
Write-Host ("State: {0}" -f $task.State)
Write-Host ("NextRunTime: {0}" -f $taskInfo.NextRunTime)
