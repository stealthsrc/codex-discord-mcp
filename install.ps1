param(
    [string]$PackageSpec = "git+https://github.com/StealthyLabsHQ/codex-discord-mcp.git",
    [string]$ConfigDir = "$HOME\.codex-disc-mcp"
)

$ErrorActionPreference = "Stop"

function Ask-Required([string]$Prompt) {
    $value = Read-Host $Prompt
    if ([string]::IsNullOrWhiteSpace($value)) {
        throw "$Prompt is required"
    }
    return $value.Trim()
}

$discordToken = Ask-Required "DISCORD_TOKEN"
$clientId = Ask-Required "DISCORD_CLIENT_ID"
$workdir = Read-Host "CODEX_WORKDIR (empty = current directory)"
if ([string]::IsNullOrWhiteSpace($workdir)) {
    $workdir = (Get-Location).Path
}

$codex = Get-Command codex -ErrorAction SilentlyContinue
$codexExecutable = if ($codex) { $codex.Source } else { "codex" }

python -m pip install --user $PackageSpec

New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null
$envPath = Join-Path $ConfigDir ".env"
$dbPath = Join-Path $ConfigDir "codex_discord.db"
$escapedWorkdir = $workdir.Replace("\", "\\")
$envLines = @(
    "DISCORD_TOKEN=$discordToken",
    "DISCORD_CLIENT_ID=$clientId",
    "CODEX_EXECUTABLE=$codexExecutable",
    "CODEX_WORKDIR=$workdir",
    "CODEX_WORKSPACES={""default"":""$escapedWorkdir""}",
    "CODEX_DEFAULT_WORKSPACE=default",
    "CODEX_DISCORD_RUNTIME=exec",
    "CODEX_DB_PATH=$dbPath",
    "ENABLE_HEALTH=1",
    "HEALTH_PORT=8080",
    "LOG_LEVEL=INFO",
    "LOG_JSON=0"
)
$envLines | Set-Content -Path $envPath -Encoding utf8

$permissions = 274877975552
$invite = "https://discord.com/oauth2/authorize?client_id=$clientId&scope=bot%20applications.commands&permissions=$permissions"

Write-Host ""
Write-Host "Config written: $envPath"
Write-Host "Invite bot:"
Write-Host $invite
Write-Host ""
Write-Host "Start bot:"
Write-Host "codex-discord-bot"
Write-Host ""
Write-Host "Start MCP server:"
Write-Host "codex-discord-mcp"
