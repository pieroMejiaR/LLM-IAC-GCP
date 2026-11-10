<#
.SYNOPSIS
  Captura en PNG una conversación de la interfaz web, para el informe.

.DESCRIPTION
  Abre la sesión con un navegador sin ventana (Edge o Chrome) y guarda la página completa. Solo lee:
  no envía nada ni presiona botones. El identificador de la sesión aparece en el registro del servidor
  (docker compose logs web-despliegue) como /api/sesiones/<id>.

.EXAMPLE
  .\scripts\capturar-sesion.ps1 -Sesion 0123456789abcdef -Salida docs\figuras\capturas\resultado.png -Todo
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [ValidatePattern('^[0-9a-f]{16}$')] [string]$Sesion,
    [Parameter(Mandatory)] [string]$Salida,
    [int]$Puerto = 8788,
    [int]$Ancho = 1440,
    [int]$Alto = 2000,
    # Abre las secciones plegadas (especificación, ficha del proyecto, hallazgos, plan completo).
    [switch]$Todo
)

$ErrorActionPreference = "Stop"
$navegador = @("${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe", "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
               "$env:ProgramFiles\Google\Chrome\Application\chrome.exe", "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe") |
    Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $navegador) { throw "No se encontró Edge ni Chrome." }

$destino = if ([IO.Path]::IsPathRooted($Salida)) { $Salida } else { Join-Path (Get-Location).Path $Salida }
$destino = [IO.Path]::GetFullPath($destino)
New-Item -ItemType Directory -Force (Split-Path $destino) | Out-Null
$perfil = Join-Path ([IO.Path]::GetTempPath()) "agente-iac-capturas"
$url = "http://127.0.0.1:$Puerto/#sesion=$Sesion" + $(if ($Todo) { "&vista=todo" } else { "" })
$argumentos = @("--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run", "--window-size=$Ancho,$Alto",
                "--virtual-time-budget=8000", "--user-data-dir=`"$perfil`"", "--screenshot=`"$destino`"", "`"$url`"")
$proceso = Start-Process -FilePath $navegador -ArgumentList $argumentos -PassThru -Wait -WindowStyle Hidden
if ($proceso.ExitCode -ne 0 -or -not (Test-Path $destino)) { throw "No se pudo capturar la sesión $Sesion." }
Write-Host "Captura guardada en $destino"
