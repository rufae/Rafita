@echo off
title Rafita Agent v5.0 - Asistente Virtual
cd /d "%~dp0"

echo ============================================
echo   Rafita Agent Core v5.0-Vision-Calendar-RAG
echo   Iniciando sistema...
echo ============================================
echo.

:: Verificar que Docker esta instalado
where docker >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Docker no encontrado. Instala Docker Desktop desde:
    echo   https://www.docker.com/products/docker-desktop/
    pause
    exit /b 1
)

:: Verificar que Docker esta corriendo
docker info >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Docker no esta corriendo. Abre Docker Desktop e intenta de nuevo.
    pause
    exit /b 1
)

:: Levantar contenedores
echo [Rafita] Levantando contenedores...
docker compose up -d
if %errorlevel% neq 0 (
    echo [ERROR] Fallo al levantar contenedores. Revisa docker-compose.yml.
    pause
    exit /b 1
)

:: Esperar a que Ollama este saludable
echo [Rafita] Esperando a que Ollama este listo...
:wait_ollama
docker inspect -f "{{.State.Health.Status}}" ollama-service 2>nul | find "healthy" >nul
if %errorlevel% neq 0 (
    timeout /t 5 /nobreak >nul
    goto wait_ollama
)
echo [Rafita] Ollama listo!

:: Verificar que el agente esta corriendo
echo [Rafita] Verificando agente...
docker inspect -f "{{.State.Status}}" rafita-agent-core 2>nul | find "running" >nul
if %errorlevel% neq 0 (
    echo [ERROR] El agente no esta corriendo. Revisa los logs con:
    echo   docker compose logs rafita-agent-core
    pause
    exit /b 1
)

echo ============================================
echo   Sistema listo! Busca a @%BOT_USERNAME% en Telegram
echo   Logs en pantalla (cierra con Ctrl+C):
echo ============================================
echo.

docker compose logs -f rafita-agent-core

pause
