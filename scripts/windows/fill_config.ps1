Param(
    [string]$ProjectRoot,
    [string]$PythonExe = "python",
    # 若填写，则先执行 merge_legacy_config.py 将旧 src/config.py 合并进本工程
    [string]$LegacyConfigPath = "",
    # 加 -FullWizard 才跑整套 configure_interactive；默认只跑「改一条存一条」的 simple_rpa_wizard
    [switch]$FullWizard
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
}
else {
    $ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
}

$configWizard = Join-Path $ProjectRoot "scripts\configure_interactive.py"
$simpleWizard = Join-Path $ProjectRoot "scripts\simple_rpa_wizard.py"
$mergeScript = Join-Path $ProjectRoot "scripts\merge_legacy_config.py"
if ($FullWizard) {
    if (-not (Test-Path -LiteralPath $configWizard)) {
        throw "找不到完整向导: $configWizard"
    }
}
else {
    if (-not (Test-Path -LiteralPath $simpleWizard)) {
        throw "找不到简易配置脚本: $simpleWizard"
    }
}

Write-Host "=== EMS 配置 ===" -ForegroundColor Cyan
Write-Host "ProjectRoot: $ProjectRoot" -ForegroundColor DarkCyan
if ($FullWizard) {
    Write-Host "模式: 完整向导 -> $configWizard" -ForegroundColor DarkGray
}
else {
    Write-Host "模式: 逐项写入 config（改一条存一条）-> $simpleWizard" -ForegroundColor DarkGray
    Write-Host "需要数据库/API 全套向导请加参数: -FullWizard" -ForegroundColor DarkYellow
}

Push-Location $ProjectRoot
try {
    if (-not [string]::IsNullOrWhiteSpace($LegacyConfigPath)) {
        if (-not (Test-Path -LiteralPath $mergeScript)) {
            throw "找不到合并脚本: $mergeScript"
        }
        if (-not (Test-Path -LiteralPath $LegacyConfigPath)) {
            throw "找不到旧配置文件: $LegacyConfigPath"
        }
        Write-Host "先从旧 config 合并: $LegacyConfigPath" -ForegroundColor Yellow
        & $PythonExe $mergeScript --legacy $LegacyConfigPath --project-root $ProjectRoot
    }
    if ($FullWizard) {
        & $PythonExe $configWizard --project-root $ProjectRoot
    }
    else {
        & $PythonExe $simpleWizard --project-root $ProjectRoot
    }
}
finally {
    Pop-Location
}
