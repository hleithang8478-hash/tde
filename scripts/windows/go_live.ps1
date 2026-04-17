Param(
    [string]$ProjectRoot,
    [string]$PythonExe = "python",
    [string]$NssmExe = "nssm",
    [string]$LegacyConfigPath = "",
    [switch]$EnableMailService,
    [switch]$SkipConfigWizard,
    [switch]$SkipPipInstall
)

$ErrorActionPreference = "Stop"

function Step($msg) {
    Write-Host "`n==== $msg ====" -ForegroundColor Cyan
}

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
}
else {
    $ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
}

# 当前正在执行的 go_live.ps1 所在工程根（脚本在 scripts\windows\ 下）
$goLiveRepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

$fillConfig = Join-Path $ProjectRoot "scripts\windows\fill_config.ps1"
$deploy = Join-Path $ProjectRoot "scripts\windows\deploy_one_click.ps1"
$verify = Join-Path $ProjectRoot "scripts\windows\verify_checklist.ps1"

Write-Host "ProjectRoot: $ProjectRoot" -ForegroundColor Cyan
if ($ProjectRoot -ne $goLiveRepoRoot) {
    Write-Host "go_live.ps1 所在工程: $goLiveRepoRoot" -ForegroundColor DarkCyan
}

if (-not (Test-Path -LiteralPath $deploy)) {
    $deployAtInvoker = Join-Path $goLiveRepoRoot "scripts\windows\deploy_one_click.ps1"
    if (Test-Path -LiteralPath $deployAtInvoker) {
        throw @"
在 -ProjectRoot 指定的目录下找不到 deploy_one_click.ps1：
  $deploy

但当前运行的 go_live.ps1 来自另一份完整工程：
  $goLiveRepoRoot

请二选一：
  1) 使用与 go_live.ps1 同一份代码的根目录作为参数，例如：
     -ProjectRoot `"$goLiveRepoRoot`"
  2) 或把 Desktop 上的工程补全为完整仓库（含 scripts\windows\*.ps1），再指向该路径。

不要混用「从 A 目录运行脚本」却「把 -ProjectRoot 指到不完整的 B 目录」。
"@
    }
}

Step "1/4 Fill config"
if ($SkipConfigWizard) {
    Write-Host "Skip config wizard (SkipConfigWizard)."
} elseif (Test-Path -LiteralPath $fillConfig) {
    if (-not [string]::IsNullOrWhiteSpace($LegacyConfigPath)) {
        & powershell -ExecutionPolicy Bypass -File $fillConfig -ProjectRoot $ProjectRoot -LegacyConfigPath $LegacyConfigPath
    } else {
        & powershell -ExecutionPolicy Bypass -File $fillConfig -ProjectRoot $ProjectRoot
    }
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
Write-Host "RPA: see scripts\RUN_EMS_执行步骤.md ; merge old config: scripts\merge_legacy_config.py --legacy ..." -ForegroundColor DarkGray
Write-Host "RPA mode: on the Windows host with broker client, run EMS:" -ForegroundColor Yellow
Write-Host "  python $ProjectRoot\scripts\run_ems.py" -ForegroundColor Yellow
Write-Host "Then test: ems_commander.py -> telemetry beat, OR python scripts\submit_order_cli.py (DB); run RPA: python scripts\run_ems.py" -ForegroundColor Yellow

Write-Host "`nGo-live flow completed." -ForegroundColor Green