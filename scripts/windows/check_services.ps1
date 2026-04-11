$services = @(
    "EMS_API",
    "EMS_RUNNER",
    "EMS_MAIL_INGEST"
)

foreach ($name in $services) {
    $svc = Get-Service -Name $name -ErrorAction SilentlyContinue
    if ($null -eq $svc) {
        Write-Host "$name : NOT INSTALLED"
    }
    else {
        Write-Host "$name : $($svc.Status)"
    }
}
