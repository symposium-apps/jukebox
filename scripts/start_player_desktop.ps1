$ErrorActionPreference = "Stop"

$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$desktopHome = Join-Path $root ".desktop"
$library = if ($env:PLAYER_DESKTOP_LIBRARY) {
    $env:PLAYER_DESKTOP_LIBRARY
} else {
    Join-Path $env:USERPROFILE "Downloads\Player"
}
$libraryMeta = Join-Path $library ".player"
$port = if ($env:PLAYER_DESKTOP_PORT) { [int]$env:PLAYER_DESKTOP_PORT } else { 8020 }
$url = "http://127.0.0.1:$port/manage"

if (!(Test-Path $library)) {
    New-Item -ItemType Directory -Force -Path $library | Out-Null
}
New-Item -ItemType Directory -Force -Path $libraryMeta | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $libraryMeta "assets") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $libraryMeta "playlists") | Out-Null

function Test-PlayerServer {
    param([int]$Port)
    try {
        $response = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/status" -TimeoutSec 1
        return $response.ok -eq $true
    } catch {
        return $false
    }
}

if (!(Test-PlayerServer -Port $port)) {
    $env:PLAYER_HOME = $libraryMeta
    $env:PLAYER_LIBRARY = $library
    $env:PLAYER_PLAYLISTS = Join-Path $libraryMeta "playlists"
    $env:PLAYER_ASSETS = Join-Path $libraryMeta "assets"
    $env:PLAYER_STATE = Join-Path $libraryMeta "state.json"
    $env:PLAYER_OLED = "0"
    $env:PLAYER_TFT = "0"

    $python = (Get-Command python -ErrorAction Stop).Source
    Start-Process -FilePath $python -WorkingDirectory $root -WindowStyle Hidden -ArgumentList @(
        "-m", "player.server",
        "--host", "127.0.0.1",
        "--port", "$port"
    ) | Out-Null

    $ready = $false
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Milliseconds 300
        if (Test-PlayerServer -Port $port) {
            $ready = $true
            break
        }
    }
    if (!$ready) {
        throw "Player Desktop server did not start on port $port."
    }
}

Start-Process $url
Write-Host "Player Desktop is running at $url"
Write-Host "Library: $library"
Write-Host "Player metadata: $libraryMeta"
