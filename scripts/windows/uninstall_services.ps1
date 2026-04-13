Param(
    [string]$NssmExe = "nssm"
)

$services = @(
    "EMS_API",
    "EMS_MAIL_INGEST"
)

foreach ($svc in $services) {
    Write-Host "Stopping $svc ..."
    & $NssmExe stop $svc

    Write-Host "Removing $svc ..."
    & $NssmExe remove $svc confirm
}

Write-Host "服务已卸载完成。"
