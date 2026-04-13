Param(
    [string]$PythonExe = "python",
    [string]$NssmExe = "nssm",
    [string]$ProjectRoot = "C:\软件\trader"
)

$apiScript = Join-Path $ProjectRoot "scripts\api_server.py"
$mailScript = Join-Path $ProjectRoot "scripts\email_ingest_runner.py"

Write-Host "Installing EMS_API ..."
& $NssmExe install EMS_API $PythonExe $apiScript
& $NssmExe set EMS_API AppDirectory $ProjectRoot
& $NssmExe set EMS_API Start SERVICE_AUTO_START
& $NssmExe set EMS_API AppStdout (Join-Path $ProjectRoot "logs\ems_api.out.log")
& $NssmExe set EMS_API AppStderr (Join-Path $ProjectRoot "logs\ems_api.err.log")

Write-Host "Installing EMS_MAIL_INGEST ..."
& $NssmExe install EMS_MAIL_INGEST $PythonExe $mailScript
& $NssmExe set EMS_MAIL_INGEST AppDirectory $ProjectRoot
& $NssmExe set EMS_MAIL_INGEST Start SERVICE_AUTO_START
& $NssmExe set EMS_MAIL_INGEST AppStdout (Join-Path $ProjectRoot "logs\ems_mail.out.log")
& $NssmExe set EMS_MAIL_INGEST AppStderr (Join-Path $ProjectRoot "logs\ems_mail.err.log")

Write-Host "Starting services..."
& $NssmExe start EMS_API
& $NssmExe start EMS_MAIL_INGEST

Write-Host "Services installed."