param(
    [string]$WorkspacePath = (Get-Location).Path
)

Write-Host "=================================================" -ForegroundColor Cyan
Write-Host " Marcus AI Migration Restore - Antigravity setup" -ForegroundColor Cyan
Write-Host "=================================================" -ForegroundColor Cyan

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# 1. Restore .env
$envSource = Join-Path $ScriptDir "workspace_secrets\.env"
if (Test-Path $envSource) {
    Copy-Item $envSource (Join-Path $WorkspacePath ".env") -Force
    Write-Host "[OK] .env ripristinato in $WorkspacePath\.env" -ForegroundColor Green
} else {
    Write-Warning "File .env non trovato in $envSource"
}

# 2. Restore SSH keys
$sshSource = Join-Path $ScriptDir "ssh_keys"
$targetSsh = Join-Path $HOME ".ssh"
if (Test-Path $sshSource) {
    if (-not (Test-Path $targetSsh)) { New-Item -ItemType Directory -Path $targetSsh -Force | Out-Null }
    Copy-Item "$sshSource\*" $targetSsh -Recurse -Force
    Write-Host "[OK] Chiavi SSH ripristinate in $targetSsh" -ForegroundColor Green
}

# 3. Restore Gemini config
$geminiSource = Join-Path $ScriptDir "gemini_config"
$targetGemini = Join-Path $HOME ".gemini"
if (Test-Path $geminiSource) {
    if (-not (Test-Path $targetGemini)) { New-Item -ItemType Directory -Path $targetGemini -Force | Out-Null }
    Copy-Item "$geminiSource\*" $targetGemini -Recurse -Force
    Write-Host "[OK] Configurazione Antigravity/.gemini ripristinata in $targetGemini" -ForegroundColor Green
}

Write-Host "`nRipristino completato con successo!" -ForegroundColor Cyan
Write-Host "Ora puoi aprire Antigravity nel workspace: $WorkspacePath" -ForegroundColor Yellow
