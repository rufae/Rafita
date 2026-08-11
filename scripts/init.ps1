<#
.SYNOPSIS
    Inicializa el proyecto Rafita Agent Core: crea .env, descarga el modelo
    Gemma 4 12B en Ollama, y levanta los contenedores Docker.
.DESCRIPTION
    Este script automatiza la configuración inicial completa del asistente
    virtual privado Rafita con Gemma 4 12B (Ollama) + Telegram.
.PARAMETER SkipPull
    Si se especifica, salta la descarga del modelo Ollama.
.PARAMETER SkipBuild
    Si se especifica, salta el build de la imagen Docker del agente.
.EXAMPLE
    .\scripts\init.ps1
    .\scripts\init.ps1 -SkipPull
    .\scripts\init.ps1 -SkipBuild -SkipPull
#>

param(
    [switch]$SkipPull,
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$PROJECT_ROOT = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
Set-Location -LiteralPath $PROJECT_ROOT

Write-Host "=" -ForegroundColor Cyan
Write-Host "  RAFITA AGENT CORE - INIT" -ForegroundColor Cyan
Write-Host "=" -ForegroundColor Cyan

# --- Step 1: Create .env from .env.example if not exists ---
$envPath = Join-Path -Path $PROJECT_ROOT -ChildPath ".env"
$envExamplePath = Join-Path -Path $PROJECT_ROOT -ChildPath ".env.example"

if (-not (Test-Path -LiteralPath $envPath)) {
    Write-Host "[1/5] Creando archivo .env desde .env.example..." -ForegroundColor Yellow
    Copy-Item -LiteralPath $envExamplePath -Destination $envPath
    Write-Host "  [+] .env creado. EDITA ESTE ARCHIVO con tu TELEGRAM_TOKEN antes de continuar." -ForegroundColor Red
    Write-Host "  [!] Abre el archivo .env y reemplaza 'tu_token_aqui' con tu token de Telegram." -ForegroundColor Yellow
} else {
    Write-Host "[1/5] .env ya existe, saltando." -ForegroundColor Green
}

# --- Step 2: Check Docker ---
Write-Host "[2/5] Verificando Docker..." -ForegroundColor Yellow
try {
    $dockerVersion = docker --version
    Write-Host "  [+] $dockerVersion" -ForegroundColor Green
} catch {
    Write-Host "  [X] Docker no está instalado o no está en PATH." -ForegroundColor Red
    Write-Host "  [!] Instala Docker Desktop desde: https://www.docker.com/products/docker-desktop/" -ForegroundColor Yellow
    exit 1
}

# --- Step 3: Check docker-compose ---
Write-Host "[3/5] Verificando docker-compose..." -ForegroundColor Yellow
try {
    $composeVersion = docker compose version
    Write-Host "  [+] $composeVersion" -ForegroundColor Green
} catch {
    Write-Host "  [X] docker-compose no está disponible." -ForegroundColor Red
    exit 1
}

# --- Step 4: Create data directories ---
Write-Host "[4/5] Creando directorios de datos..." -ForegroundColor Yellow
@("data\db", "data\excels", "data\exports", "data\logs", "ollama\models") | ForEach-Object {
    $p = Join-Path -Path $PROJECT_ROOT -ChildPath $_
    if (-not (Test-Path -LiteralPath $p)) {
        New-Item -ItemType Directory -Path $p -Force | Out-Null
        Write-Host "  [+] Creado: $_" -ForegroundColor Green
    }
}

# --- Step 5: Pull Ollama model (optional) ---
if (-not $SkipPull) {
    Write-Host "[5/5] Descargando modelo Gemma 4 12B en Ollama..." -ForegroundColor Yellow
    Write-Host "  [!] Esto puede tomar varios minutos (8-10 GB de descarga)." -ForegroundColor Yellow

    docker pull ollama/ollama:latest
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [X] Error al descargar la imagen de Ollama." -ForegroundColor Red
        exit 1
    }

    docker run -d --name ollama-init `
        -v "${PROJECT_ROOT}\ollama\models:/root/.ollama" `
        ollama/ollama:latest

    Write-Host "  Descargando gemma4:12b-instruct-q4_K_M..." -ForegroundColor Yellow
    docker exec ollama-init ollama pull gemma4:12b-instruct-q4_K_M
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [X] Error al descargar el modelo. Verifica que exista en ollama.com/library" -ForegroundColor Red
        docker stop ollama-init
        docker rm ollama-init
        exit 1
    }

    docker stop ollama-init
    docker rm ollama-init
    Write-Host "  [+] Modelo descargado exitosamente." -ForegroundColor Green
} else {
    Write-Host "[5/5] Descarga de modelo saltada (SkipPull)." -ForegroundColor Yellow
}

# --- Build and start ---
Write-Host ""
Write-Host "Iniciando contenedores..." -ForegroundColor Cyan

$buildFlag = if (-not $SkipBuild) { "--build" } else { "" }
docker compose up -d $buildFlag

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "=" -ForegroundColor Cyan
    Write-Host "  RAFITA AGENT CORE - INICIADO" -ForegroundColor Green
    Write-Host "=" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  Comandos útiles:" -ForegroundColor White
    Write-Host "  docker compose logs -f rafita-agent-core   -> Ver logs del agente" -ForegroundColor Gray
    Write-Host "  docker compose logs -f ollama-service       -> Ver logs de Ollama" -ForegroundColor Gray
    Write-Host "  docker compose down                          -> Detener todo" -ForegroundColor Gray
    Write-Host "  docker compose restart rafita-agent-core     -> Reiniciar agente" -ForegroundColor Gray
    Write-Host ""
} else {
    Write-Host "  [X] Error al iniciar los contenedores." -ForegroundColor Red
    exit 1
}
