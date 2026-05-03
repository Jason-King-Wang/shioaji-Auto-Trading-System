param(
    [string]$TaskName = "SinoPac2330BuyIntradayOdd0910"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host ("Scheduled task '{0}' removed." -f $TaskName)
} else {
    Write-Host ("Scheduled task '{0}' was not found." -f $TaskName)
}
