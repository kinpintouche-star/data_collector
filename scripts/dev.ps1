param(
    [ValidateSet("up", "down", "restart", "ps", "logs", "logs-api", "logs-web", "migrate", "seed", "urls")]
    [string] $Command = "up"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Invoke-Compose {
    if (Get-Command docker-compose -ErrorAction SilentlyContinue) {
        & docker-compose @args
    }
    else {
        & docker compose @args
    }
}

function Show-Urls {
    Write-Host ""
    Write-Host "ICT Trading Lab" -ForegroundColor Cyan
    Write-Host "  Web:      http://127.0.0.1:5173"
    Write-Host "  API:      http://127.0.0.1:8000/api/health"
    Write-Host "  Adminer:  http://127.0.0.1:8080"
    Write-Host "  Postgres: localhost:5432"
    Write-Host ""
}

switch ($Command) {
    "up" {
        Invoke-Compose up -d --build postgres adminer api web
        Show-Urls
        Invoke-Compose ps
    }
    "down" {
        Invoke-Compose down
    }
    "restart" {
        Invoke-Compose restart api web
        Show-Urls
        Invoke-Compose ps
    }
    "ps" {
        Invoke-Compose ps
    }
    "logs" {
        Invoke-Compose logs -f --tail=120 api web postgres
    }
    "logs-api" {
        Invoke-Compose logs -f --tail=160 api
    }
    "logs-web" {
        Invoke-Compose logs -f --tail=160 web
    }
    "migrate" {
        Invoke-Compose run --rm api python -m alembic upgrade head
    }
    "seed" {
        Invoke-Compose run --rm api python -m ict.cli db seed-defaults
    }
    "urls" {
        Show-Urls
    }
}
