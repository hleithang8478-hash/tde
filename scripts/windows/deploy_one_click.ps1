Param(
    [string]$ProjectRoot = "C:\software\trader",
    [string]$PythonExe = "python",
    [string]$NssmExe = "nssm",
    [switch]$EnableMailService,
    [switch]$SkipPipInstall,
    [switch]$RunDbMigration
)

$ErrorActionPreference = "Stop"

function Write-Step($msg) {
    Write-Host "`n==== $msg ====" -ForegroundColor Cyan
}

function Assert-CommandExists($cmd, $displayName) {
    $found = Get-Command $cmd -ErrorAction SilentlyContinue
    if ($null -eq $found) {
        throw "Command not found: $displayName ($cmd)."
    }
}

function Ensure-Dir($path) {
    if (-not (Test-Path -LiteralPath $path)) {
        New-Item -Path $path -ItemType Directory | Out-Null
    }
}

Write-Step "1/9 Check base path"
if (-not (Test-Path -LiteralPath $ProjectRoot)) {
    throw "ProjectRoot not found: $ProjectRoot"
}

$reqFile = Join-Path $ProjectRoot "requirements.txt"
$configFile = Join-Path $ProjectRoot "src\config.py"
$installSvcFile = Join-Path $ProjectRoot "scripts\windows\install_services.ps1"
$apiFile = Join-Path $ProjectRoot "scripts\api_server.py"
$bridgeFile = Join-Path $ProjectRoot "scripts\ptrade_bridge_template.py"
$migrationPy = Join-Path $ProjectRoot "scripts\run_migrations.py"

$mustFiles = @($reqFile, $configFile, $installSvcFile, $apiFile, $bridgeFile, $migrationPy)
foreach ($f in $mustFiles) {
    if (-not (Test-Path -LiteralPath $f)) {
        throw "Missing required file: $f"
    }
}

Write-Step "2/9 Check commands"
Assert-CommandExists $PythonExe "Python"
Assert-CommandExists $NssmExe "NSSM"

Write-Step "3/9 Create logs dir"
$logsDir = Join-Path $ProjectRoot "logs"
Ensure-Dir $logsDir

Write-Step "4/9 Install Python dependencies"
if (-not $SkipPipInstall) {
    Push-Location $ProjectRoot
    & $PythonExe -m pip install --upgrade pip
    & $PythonExe -m pip install -r $reqFile
    Pop-Location
} else {
    Write-Host "Skip pip install."
}

Write-Step "5/9 DB migration (optional)"
if ($RunDbMigration) {
    Push-Location $ProjectRoot
    & $PythonExe $migrationPy
    Pop-Location
} else {
    Write-Host "Skip DB migration."
}

Write-Step "6/9 Install and start services"
& powershell -ExecutionPolicy Bypass -File $installSvcFile -PythonExe $PythonExe -NssmExe $NssmExe -ProjectRoot $ProjectRoot

if (-not $EnableMailService) {
    Write-Host "Disable EMS_MAIL_INGEST by default."
    & $NssmExe stop EMS_MAIL_INGEST | Out-Null
}

Write-Step "7/9 API health check"
$healthUrl = "http://127.0.0.1:18080/health"
$ok = $false
for ($i = 1; $i -le 8; $i++) {
    try {
        $resp = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 3
        if ($resp.StatusCode -eq 200) {
            $ok = $true
            Write-Host "API is healthy: $healthUrl"
            break
        }
    } catch {
        Start-Sleep -Seconds 2
    }
}
if (-not $ok) {
    Write-Warning "API health check failed. See logs\\ems_api.err.log"
}

Write-Step "8/9 Print service status"
$services = @("EMS_API", "EMS_MAIL_INGEST")
foreach ($svc in $services) {
    $s = Get-Service -Name $svc -ErrorAction SilentlyContinue
    if ($null -eq $s) {
        Write-Host "$svc : NOT INSTALLED" -ForegroundColor Yellow
    } else {
        Write-Host "$svc : $($s.Status)"
    }
}

Write-Step "9/9 Next steps"
Write-Host "Open Ptrade strategy editor and run:"
Write-Host "  $bridgeFile"
Write-Host "Do not run EMS_RUNNER as Windows service."

Write-Host "`nDeploy finished." -ForegroundColor Green
