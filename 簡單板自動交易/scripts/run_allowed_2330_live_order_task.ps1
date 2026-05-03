param(
    [string]$PythonPath = "",
    [switch]$SmokeTest
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptDir
$runPy = Join-Path $projectRoot "run.py"
$logDirName = if ($SmokeTest) { "allowed_2330_live_order_smoke" } else { "allowed_2330_live_order" }
$logDir = Join-Path $projectRoot ("data\task_logs\{0}" -f $logDirName)

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$timestamp = Get-Date -Format "yyyy-MM-dd_HHmmss"
$logPath = Join-Path $logDir "$timestamp.log"

if ($PythonPath -and (Test-Path $PythonPath)) {
    $python = $PythonPath
} else {
    $python = (Get-Command python.exe -ErrorAction Stop).Source
}

Set-Location $projectRoot
if (-not $SmokeTest) {
    $env:AUTO_TRADE_LIVE = "1"
}
$env:PYTHONIOENCODING = "utf-8"

$taskMode = if ($SmokeTest) { "smoke test" } else { "task" }
"[$(Get-Date -Format s)] starting $taskMode SinoPac2330BuyIntradayOdd0910" | Tee-Object -FilePath $logPath
"[$(Get-Date -Format s)] python=$python" | Tee-Object -FilePath $logPath -Append

$stdoutPath = $null
$stderrPath = $null

try {
    $stdoutPath = Join-Path $env:TEMP ("sinopac_allowed_live_order_{0}_stdout.log" -f [guid]::NewGuid().ToString("N"))
    $stderrPath = Join-Path $env:TEMP ("sinopac_allowed_live_order_{0}_stderr.log" -f [guid]::NewGuid().ToString("N"))

    $processInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $processInfo.FileName = $python
    $processInfo.WorkingDirectory = $projectRoot
    $processInfo.UseShellExecute = $false
    $processInfo.RedirectStandardOutput = $true
    $processInfo.RedirectStandardError = $true
    $quotedRunPy = '"' + $runPy.Replace('"', '\"') + '"'
    if ($SmokeTest) {
        $processInfo.Arguments = '-c "import sys; print(''smoke_ok''); print(sys.argv[1])" ' + $quotedRunPy
    } else {
        $processInfo.Arguments = "$quotedRunPy run_allowed_live_order"
    }

    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $processInfo
    [void]$process.Start()
    $stdout = $process.StandardOutput.ReadToEnd()
    $stderr = $process.StandardError.ReadToEnd()
    $process.WaitForExit()

    Set-Content -LiteralPath $stdoutPath -Value $stdout -Encoding UTF8
    Set-Content -LiteralPath $stderrPath -Value $stderr -Encoding UTF8

    if (Test-Path -LiteralPath $stdoutPath) {
        Get-Content -LiteralPath $stdoutPath | Tee-Object -FilePath $logPath -Append
    }
    if (Test-Path -LiteralPath $stderrPath) {
        Get-Content -LiteralPath $stderrPath | Tee-Object -FilePath $logPath -Append
    }

    $exitCode = $process.ExitCode
    "[$(Get-Date -Format s)] exit_code=$exitCode" | Tee-Object -FilePath $logPath -Append
    exit $exitCode
} catch {
    $_ | Out-String | Tee-Object -FilePath $logPath -Append
    "[$(Get-Date -Format s)] exit_code=1" | Tee-Object -FilePath $logPath -Append
    exit 1
} finally {
    if ($stdoutPath -and (Test-Path -LiteralPath $stdoutPath)) {
        Remove-Item -LiteralPath $stdoutPath -ErrorAction SilentlyContinue
    }
    if ($stderrPath -and (Test-Path -LiteralPath $stderrPath)) {
        Remove-Item -LiteralPath $stderrPath -ErrorAction SilentlyContinue
    }
}
