# Requires UTF-8 with BOM so Windows PowerShell 5.x parses correctly on zh-CN locale.
# One-shot: EMS loop + nssm restart EMS_API + foreground api_server + ems_commander (optional switches).
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\ems_start_all.ps1
# Optional:
#   -PythonExe "python" -NssmExe "nssm" -ProjectRoot "D:\path\to\trader"
#   -SkipNssm -SkipApiServer -SkipEms -SkipCommander

Param(
    [string]$PythonExe   = "python",
    [string]$NssmExe     = "nssm",
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")),
    [switch]$SkipNssm,
    [switch]$SkipApiServer,
    [switch]$SkipEms,
    [switch]$SkipCommander
)

$ErrorActionPreference = "Continue"
$ProjectRoot = (Resolve-Path $ProjectRoot).Path

$RunEms      = Join-Path $ProjectRoot "scripts\run_ems.py"
$ApiServer   = Join-Path $ProjectRoot "scripts\api_server.py"
$Commander   = Join-Path $ProjectRoot "ems_commander.py"

function Test-FileExists($Path, $Label) {
    if (-not (Test-Path $Path)) {
        Write-Host ("[ems_start_all] Missing {0}: {1}" -f $Label, $Path) -ForegroundColor Yellow
        return $false
    }
    return $true
}

function Start-InNewWindow([string]$Title, [string]$Cmd) {
    # Use -EncodedCommand so characters like | in the title never get parsed as a pipeline.
    # (Start-Process -ArgumentList -Command is fragile with pipes/quotes.)
    $safeTitle = ($Title -replace "'", "''")
    $scriptLine = "`$Host.UI.RawUI.WindowTitle = '$safeTitle'; $Cmd"
    $bytes = [System.Text.Encoding]::Unicode.GetBytes($scriptLine)
    $encoded = [Convert]::ToBase64String($bytes)
    Start-Process -FilePath "powershell.exe" `
        -ArgumentList "-NoExit", "-NoProfile", "-EncodedCommand", $encoded `
        -WorkingDirectory $ProjectRoot | Out-Null
    Write-Host ("[ems_start_all] Started window: {0}" -f $Title) -ForegroundColor Cyan
}

Write-Host ("[ems_start_all] ProjectRoot = {0}" -f $ProjectRoot)
Write-Host ("[ems_start_all] PythonExe   = {0}" -f $PythonExe)
Write-Host ("[ems_start_all] NssmExe     = {0}" -f $NssmExe)

# 1) nssm restart EMS_API
if (-not $SkipNssm) {
    $nssm = (Get-Command $NssmExe -ErrorAction SilentlyContinue)
    if ($null -eq $nssm) {
        Write-Host ("[ems_start_all] NSSM not found ({0}); skip service restart. https://nssm.cc/" -f $NssmExe) -ForegroundColor Yellow
    } else {
        Write-Host "[ems_start_all] nssm restart EMS_API ..." -ForegroundColor Green
        & $NssmExe restart EMS_API
        if ($LASTEXITCODE -ne 0) {
            Write-Host ("[ems_start_all] nssm restart EMS_API failed, exit={0}" -f $LASTEXITCODE) -ForegroundColor Yellow
        }
    }
} else {
    Write-Host "[ems_start_all] Skipped nssm restart EMS_API" -ForegroundColor DarkGray
}

# 2) run_ems.py
if (-not $SkipEms) {
    if (Test-FileExists $RunEms "scripts\run_ems.py") {
        Start-InNewWindow "EMS - run_ems" ("& '" + $PythonExe + "' '" + $RunEms + "'")
    }
} else {
    Write-Host "[ems_start_all] Skipped run_ems.py" -ForegroundColor DarkGray
}

# 3) api_server.py (foreground)
if (-not $SkipApiServer) {
    if (Test-FileExists $ApiServer "scripts\api_server.py") {
        Start-InNewWindow "EMS - api_server" ("& '" + $PythonExe + "' '" + $ApiServer + "'")
    }
} else {
    Write-Host "[ems_start_all] Skipped api_server.py" -ForegroundColor DarkGray
}

# 4) ems_commander.py
if (-not $SkipCommander) {
    if (Test-FileExists $Commander "ems_commander.py") {
        Start-InNewWindow "EMS - ems_commander" ("& '" + $PythonExe + "' '" + $Commander + "'")
    }
} else {
    Write-Host "[ems_start_all] Skipped ems_commander.py" -ForegroundColor DarkGray
}

Write-Host "[ems_start_all] Done. Leave child windows open for logs." -ForegroundColor Green
