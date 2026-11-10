<#
.SYNOPSIS
  Publica en Artifact Registry las imágenes de una aplicación que está en esta computadora.

.DESCRIPTION
  Paso previo al despliegue desde el chat. Cloud Run ejecuta imágenes de contenedor que deben estar en
  el registro de Google; el agente no las puede construir porque corre en un sandbox sin Docker ni
  internet. Este script lo hace en la máquina de quien despliega, con Docker y gcloud.

  Cada imagen se indica como nombre=Dockerfile, con la ruta del Dockerfile relativa a la carpeta de la
  aplicación. El contexto de construcción es esa carpeta.

.EXAMPLE
  .\scripts\publicar-imagenes.ps1 -Carpeta E:\LLM-IAC\picanteria-digital `
      -Imagen picanteria-api=apps/api/Dockerfile, picanteria-web=apps/web/Dockerfile

.EXAMPLE
  .\scripts\publicar-imagenes.ps1 -Carpeta C:\mi-app -Imagen mi-app=Dockerfile -Simular
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string]$Carpeta,
    [Parameter(Mandatory)] [string[]]$Imagen,
    [string]$Etiqueta = "v1",
    # Por defecto: <GCP_REGION>-docker.pkg.dev/<GCP_PROYECTO>/apps, leídos del .env del agente.
    [string]$Repositorio,
    # Muestra los comandos sin ejecutarlos.
    [switch]$Simular
)

$ErrorActionPreference = "Stop"

function Leer-Env([string]$nombre) {
    $ruta = Join-Path (Split-Path $PSScriptRoot -Parent) ".env"
    if (-not (Test-Path $ruta)) { return $null }
    $linea = Get-Content $ruta | Where-Object { $_ -match "^\s*$nombre=(.+)$" } | Select-Object -First 1
    if ($linea) { return ($linea -split "=", 2)[1].Trim().Trim('"').Trim("'") }
    return $null
}

if (-not (Test-Path $Carpeta -PathType Container)) { throw "No existe la carpeta de la aplicación: $Carpeta" }
$Carpeta = (Resolve-Path $Carpeta).Path

if (-not $Repositorio) {
    $proyecto = Leer-Env "GCP_PROYECTO"
    $region = Leer-Env "GCP_REGION"
    if (-not $region) { $region = "southamerica-west1" }
    if (-not $proyecto) { throw "Indique -Repositorio o defina GCP_PROYECTO en el .env del agente." }
    $Repositorio = "$region-docker.pkg.dev/$proyecto/apps"
}
$registro = $Repositorio.Split("/")[0]

# Con -File, varias imágenes separadas por coma llegan como un solo texto.
$Imagen = @($Imagen | ForEach-Object { $_ -split "," } | ForEach-Object { $_.Trim() } | Where-Object { $_ })

$pares = foreach ($item in $Imagen) {
    $partes = $item -split "=", 2
    $nombre = $partes[0].Trim()
    $dockerfile = if ($partes.Count -gt 1 -and $partes[1].Trim()) { $partes[1].Trim() } else { "Dockerfile" }
    if ($nombre -notmatch '^[a-z0-9][a-z0-9._-]*$') { throw "Nombre de imagen no válido: '$nombre' (minúsculas, números, punto, guion)." }
    $ruta = Join-Path $Carpeta $dockerfile
    if (-not (Test-Path $ruta -PathType Leaf)) { throw "No existe el Dockerfile de '$nombre': $ruta" }
    [pscustomobject]@{ Nombre = $nombre; Dockerfile = $ruta; Destino = "$Repositorio/${nombre}:$Etiqueta" }
}

if (-not $Simular) {
    foreach ($programa in "docker", "gcloud") {
        if (-not (Get-Command $programa -ErrorAction SilentlyContinue)) { throw "No se encontró '$programa' en el PATH." }
    }
    docker info --format '{{.ServerVersion}}' *> $null
    if ($LASTEXITCODE -ne 0) { throw "Docker no responde. Abra Docker Desktop y vuelva a intentar." }
}

function Ejecutar([string]$programa, [string[]]$argumentos) {
    Write-Host "> $programa $($argumentos -join ' ')" -ForegroundColor DarkGray
    if ($Simular) { return }
    & $programa @argumentos
    if ($LASTEXITCODE -ne 0) { throw "Falló: $programa $($argumentos -join ' ')" }
}

Write-Host "Repositorio: $Repositorio"
Ejecutar "gcloud" @("auth", "configure-docker", $registro, "--quiet")
foreach ($p in $pares) {
    Write-Host "`nImagen $($p.Nombre)" -ForegroundColor Cyan
    Ejecutar "docker" @("build", "-t", $p.Destino, "-f", $p.Dockerfile, $Carpeta)
    Ejecutar "docker" @("push", $p.Destino)
}

Write-Host "`n$(if ($Simular) { 'Simulación: no se ejecutó nada. Las imágenes quedarían en:' } else { 'Listo. Imágenes publicadas:' })"
foreach ($p in $pares) { Write-Host "  $($p.Destino)" }
Write-Host "Use esas rutas en el requerimiento del chat de despliegue (http://127.0.0.1:8788)."
