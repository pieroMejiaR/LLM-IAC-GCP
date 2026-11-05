"""Evidencia de las pruebas S5 y S6 y de las versiones del sandbox (sección 4.1 y tabla 5.8).

Se corre dentro del contenedor del agente:

    docker compose run --rm --entrypoint python agente -m sandbox.pruebas_seguridad s6
    docker compose run --rm --entrypoint python agente -m sandbox.pruebas_seguridad s5
    docker compose run --rm --entrypoint python agente -m sandbox.pruebas_seguridad versiones

Cada comando imprime lo que intenta y lo que obtuvo, y guarda el mismo texto en
resultados/evidencia/<prueba>-<fecha>.txt. Termina con código 0 solo si todo dio
lo esperado.

S5 prueba la capa de IAM: se salta a propósito la capa de comandos (S1) y ejecuta
`terraform apply` con la cuenta Viewer, que debe fallar por permisos. Si por error
la cuenta lograra crear el bucket, el script lo destruye de inmediato y reporta FALLA.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import platform
import socket
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import uuid
from importlib import metadata
from pathlib import Path

from agent.config import Config
from sandbox.local_runner import _entorno_base, versiones_linters

DIR_EVIDENCIA = Path("resultados/evidencia")
DIR_PROVEEDORES = Path("/opt/terraform/plugins/registry.terraform.io/hashicorp")


class Registro:
    def __init__(self, prueba: str, titulo: str) -> None:
        self.prueba = prueba
        self.lineas: list[str] = []
        self.resultados: list[bool] = []
        ahora = dt.datetime.now().astimezone()
        self.sello = ahora.strftime("%Y%m%d-%H%M%S")
        self.escribir(f"{prueba} · {titulo}")
        self.escribir(f"Fecha: {ahora.isoformat(timespec='seconds')}   Contenedor: {socket.gethostname()}   "
                      f"Usuario: uid={os.getuid() if hasattr(os, 'getuid') else '?'}")
        self.escribir(f"Proxy de salida: {os.environ.get('HTTPS_PROXY') or '(ninguno)'}")
        self.escribir("")

    def escribir(self, texto: str = "") -> None:
        print(texto, flush=True)
        self.lineas.append(texto)

    def comprobar(self, descripcion: str, esperado: str, obtenido: str, ok: bool) -> None:
        self.resultados.append(ok)
        self.escribir(f"[{'OK' if ok else 'FALLA'}] {descripcion}")
        self.escribir(f"       esperado: {esperado}")
        self.escribir(f"       obtenido: {obtenido}")

    def cerrar(self) -> int:
        bien = sum(self.resultados)
        total = len(self.resultados)
        self.escribir("")
        self.escribir(f"Resultado: {bien}/{total} comprobaciones dieron lo esperado"
                      + ("" if bien == total else "  <-- REVISAR"))
        DIR_EVIDENCIA.mkdir(parents=True, exist_ok=True)
        archivo = DIR_EVIDENCIA / f"{self.prueba}-{self.sello}.txt"
        archivo.write_text("\n".join(self.lineas) + "\n", encoding="utf-8")
        print(f"Evidencia guardada en {archivo}")
        return 0 if total and bien == total else 1


# --------------------------------------------------------------------------- S6

def _https(url: str, timeout: float = 15) -> tuple[str, int | None]:
    """Devuelve (descripción, código HTTP o None si no hubo respuesta del destino)."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return f"HTTP {r.status} del destino", r.status
    except urllib.error.HTTPError as e:
        squid = e.headers.get("X-Squid-Error") if e.headers else None
        return (f"HTTP {e.code}" + (f" del proxy ({squid})" if squid else " del destino")), (None if squid else e.code)
    except urllib.error.URLError as e:
        return f"rechazado: {e.reason}", None
    except OSError as e:
        return f"rechazado: {type(e).__name__}: {e}", None


def s6() -> int:
    reg = Registro("S6", "El contenedor no sale a internet")

    for host, puerto in [("1.1.1.1", 443), ("8.8.8.8", 53), ("142.250.0.1", 443)]:
        try:
            socket.create_connection((host, puerto), timeout=5).close()
            obtenido, ok = "conexión establecida", False
        except OSError as e:
            obtenido, ok = f"{type(e).__name__}: {e}", True
        reg.comprobar(f"Conexión TCP directa a {host}:{puerto}, sin proxy", "rechazada", obtenido, ok)

    try:
        ip = socket.getaddrinfo("example.com", 443)[0][4][0]
        reg.escribir(f"(info) DNS directo de example.com: resuelve a {ip}; la conexión igual queda bloqueada")
    except OSError as e:
        reg.escribir(f"(info) DNS directo de example.com: no resuelve ({e})")
    reg.escribir()

    rechazos = [
        ("https://example.com", "HTTPS a un sitio cualquiera"),
        ("https://registry.terraform.io/v1/providers/hashicorp/aws/versions", "Registro de Terraform (otros proveedores)"),
        ("https://github.com", "GitHub (módulos remotos)"),
        ("https://raw.githubusercontent.com/BerriAI/litellm/main/README.md", "Descargas de paquetes o scripts"),
        ("https://8.8.8.8", "IP literal, sin nombre de dominio"),
        ("http://storage.googleapis.com/", "HTTP sin cifrar, aunque sea a un dominio permitido"),
    ]
    for url, descripcion in rechazos:
        obtenido, codigo = _https(url)
        reg.comprobar(f"{descripcion}: {url}", "rechazado por el proxy (403)", obtenido,
                      codigo is None and ("403" in obtenido or "rechazado" in obtenido))

    reg.escribir()
    url = "https://www.googleapis.com/discovery/v1/apis?name=storage&fields=kind"
    obtenido, codigo = _https(url)
    reg.comprobar(f"Control: las APIs de Google sí pasan por el proxy ({url})",
                  "responde el destino (Gemini y terraform plan siguen funcionando)", obtenido, codigo is not None)
    return reg.cerrar()


# --------------------------------------------------------------------------- S5

_PLANTILLA_S5 = {
    "versions.tf": 'terraform {\n  required_providers {\n    google = { source = "hashicorp/google" }\n  }\n}\n',
    "main.tf": (
        'variable "project_id" {\n  type = string\n}\n\nvariable "region" {\n  type = string\n}\n\n'
        'provider "google" {\n  project = var.project_id\n  region  = var.region\n}\n\n'
        'resource "google_storage_bucket" "s5" {\n  name                        = "NOMBRE"\n'
        '  location                    = var.region\n  uniform_bucket_level_access = true\n'
        '  public_access_prevention    = "enforced"\n  force_destroy               = true\n}\n'
    ),
}


def _terraform(args: list[str], cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(["terraform", *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=900)


def _resumen(texto: str, maximo: int = 6) -> str:
    lineas = [ln.strip() for ln in texto.splitlines() if ln.strip()]
    claves = [ln for ln in lineas if any(p in ln for p in ("Error", "403", "denied", "Permission", "does not have"))]
    return " | ".join((claves or lineas)[:maximo])[:900]


def s5() -> int:
    cfg = Config.desde_entorno()
    reg = Registro("S5", "La cuenta Viewer no puede crear recursos")
    if not (cfg.proyecto_gcp and cfg.cuenta_evaluacion):
        reg.comprobar("Configuración", "GCP_PROYECTO y GCP_CUENTA_EVALUACION definidos en .env",
                      "faltan variables", False)
        return reg.cerrar()
    if not os.path.exists(os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")):
        reg.escribir("Aviso: GOOGLE_APPLICATION_CREDENTIALS no apunta a un archivo; ejecute "
                     "`gcloud auth application-default login` en el equipo.")

    nombre = f"{cfg.proyecto_gcp}-s5-{uuid.uuid4().hex[:6]}"[:63]
    reg.escribir(f"Proyecto: {cfg.proyecto_gcp}   Región: {cfg.region}")
    reg.escribir(f"Cuenta suplantada: {cfg.cuenta_evaluacion}")
    reg.escribir(f"Bucket que se intenta crear: {nombre}")
    reg.escribir()

    env = _entorno_base(cfg.proyecto_gcp, cfg.region, cfg.cuenta_evaluacion)
    with tempfile.TemporaryDirectory(prefix="s5-") as tmp:
        d = Path(tmp)
        for archivo, contenido in _PLANTILLA_S5.items():
            (d / archivo).write_text(contenido.replace("NOMBRE", nombre), encoding="utf-8")

        p = _terraform(["init", "-input=false", "-no-color"], d, env)
        reg.comprobar("terraform init (proveedor del espejo local)", "termina bien", f"código {p.returncode}",
                      p.returncode == 0)
        if p.returncode:
            reg.escribir(_resumen(p.stdout + p.stderr))
            return reg.cerrar()

        p = _terraform(["plan", "-input=false", "-lock=false", "-no-color"], d, env)
        reg.comprobar("terraform plan con la cuenta Viewer (lo que sí hace el modo evaluación)",
                      "termina bien: la cuenta puede leer", f"código {p.returncode}. {_resumen(p.stdout + p.stderr, 2)}",
                      p.returncode == 0)

        p = _terraform(["apply", "-input=false", "-no-color", "-auto-approve"], d, env)
        salida = p.stdout + p.stderr
        permiso = any(x in salida for x in ("403", "storage.buckets.create", "Permission", "does not have"))
        reg.comprobar("terraform apply con la cuenta Viewer (crear el bucket)",
                      "falla por permisos (403, storage.buckets.create)",
                      f"código {p.returncode}. {_resumen(salida)}", p.returncode != 0 and permiso)

        estado = _terraform(["state", "list"], d, env).stdout.split()
        reg.comprobar("Estado de Terraform después del apply", "sin recursos creados",
                      ", ".join(estado) or "vacío", not estado)
        if estado:
            p = _terraform(["destroy", "-input=false", "-no-color", "-auto-approve"], d, env)
            reg.escribir(f"Se destruyó el bucket creado por error: código {p.returncode}. {_resumen(p.stdout + p.stderr)}")
    return reg.cerrar()


# --------------------------------------------------------------------------- versiones

def versiones() -> int:
    reg = Registro("versiones", "Versiones del sandbox (sección 4.1)")
    p = subprocess.run(["terraform", "version", "-json"], capture_output=True, text=True, timeout=60)
    tf = json.loads(p.stdout).get("terraform_version") if p.returncode == 0 else None
    proveedores = {d.name: sorted(v.name for v in d.iterdir()) for d in sorted(DIR_PROVEEDORES.iterdir())} \
        if DIR_PROVEEDORES.exists() else {}
    filas = [("Terraform", tf), *((f"Proveedor hashicorp/{k}", ", ".join(v)) for k, v in proveedores.items())]
    lint = versiones_linters(os.environ.get("TFLINT_CONFIG"))
    filas += [("TFLint", lint.get("tflint")), ("Ruleset google de TFLint", lint.get("tflint-ruleset-google")),
              ("Checkov", lint.get("checkov")), ("Python", platform.python_version())]
    for paquete in ("litellm", "pydantic", "typer", "pyyaml", "pytest"):
        try:
            filas.append((paquete, metadata.version(paquete)))
        except metadata.PackageNotFoundError:
            filas.append((paquete, None))
    for nombre, version in filas:
        reg.resultados.append(bool(version))
        reg.escribir(f"  {nombre:<34} {version or 'NO ENCONTRADO'}")
    return reg.cerrar()


def main(argv: list[str]) -> int:
    comandos = {"s5": s5, "s6": s6, "versiones": versiones}
    if len(argv) != 1 or argv[0] not in comandos:
        print(__doc__)
        return 2
    return comandos[argv[0]]()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
