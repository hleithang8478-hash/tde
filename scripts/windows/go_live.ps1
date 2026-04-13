Param(
    [string]$ProjectRoot,
    [string]$PythonExe = "python",
    [string]$NssmExe = "nssm",
    [switch]$EnableMailService,
    [switch]$SkipConfigWizard,
    [switch]$SkipPipInstall
)

$ErrorActionPreference = "Stop"

function Step($msg) {
    Write-Host "`n==== $msg ====" -ForegroundColor Cyan
}

# Auto-detect project root from script location to avoid non-ASCII path encoding issues
if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
}

$fillConfig = Join-Path $ProjectRoot "scripts\windows\fill_config.ps1"
$deploy = Join-Path $ProjectRoot "scripts\windows\deploy_one_click.ps1"
$verify = Join-Path $ProjectRoot "scripts\windows\verify_checklist.ps1"

Write-Host "ProjectRoot: $ProjectRoot"

Step "1/4 Fill config"
if ($SkipConfigWizard) {
    Write-Host "Skip config wizard (SkipConfigWizard)."
} elseif (Test-Path -LiteralPath $fillConfig) {
    & powershell -ExecutionPolicy Bypass -File $fillConfig -ProjectRoot $ProjectRoot
} else {
    Write-Warning "Missing optional script: $fillConfig, continue."
}

Step "2/4 Deploy (with DB migration)"
if (-not (Test-Path -LiteralPath $deploy)) {
    throw "Missing required script: $deploy"
}

$deployArgs = @(
    "-ExecutionPolicy", "Bypass",
    "-File", $deploy,
    "-ProjectRoot", $ProjectRoot,
    "-PythonExe", $PythonExe,
    "-NssmExe", $NssmExe,
    "-RunDbMigration"
)
if ($EnableMailService) { $deployArgs += "-EnableMailService" }
if ($SkipPipInstall) { $deployArgs += "-SkipPipInstall" }

& powershell @deployArgs

Step "3/4 Verify"
if (Test-Path -LiteralPath $verify) {
    & powershell -ExecutionPolicy Bypass -File $verify -ProjectRoot $ProjectRoot -DeepConfigCheck -CheckSystemTuning
} else {
    Write-Warning "Missing optional script: $verify, continue."
}

Step "4/4 Next"
Write-Host "Open Ptrade strategy editor, run bridge script:" -ForegroundColor Yellow
Write-Host "  $ProjectRoot\scripts\ptrade_bridge_template.py" -ForegroundColor Yellow
Write-Host "Then run one test order via send_order_api.py" -ForegroundColor Yellow

Write-Host "`nGo-live flow completed." -ForegroundColor Green
