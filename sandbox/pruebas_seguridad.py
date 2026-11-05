"""Evidencia de las pruebas S5 y S6, versiones del sandbox y diagnóstico del entorno.

Se corre dentro del contenedor del agente:

    docker compose run --rm --entrypoint python agente -m sandbox.pruebas_seguridad diagnostico
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

    def comprobar(self, descripcion: str, esperado: str, obtenido: str, ok: bool, *, aviso: bool = False,
                  ayuda: str = "") -> None:
        """aviso=True: si no se cumple se informa, pero no cuenta como falla."""
        if not aviso:
            self.resultados.append(ok)
        self.escribir(f"[{'OK' if ok else ('AVISO' if aviso else 'FALLA')}] {descripcion}")
        self.escribir(f"       esperado: {esperado}")
        self.escribir(f"       obtenido: {obtenido}")
        if ayuda and not ok:
            self.escribir(f"       qué hacer: {ayuda}")

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


# --------------------------------------------------------------------------- diagnóstico

_PLANTILLA_DIAGNOSTICO = (
    'terraform {\n  required_providers {\n    google = { source = "hashicorp/google" }\n  }\n}\n\n'
    'variable "project_id" {\n  type = string\n}\n\nvariable "region" {\n  type = string\n}\n\n'
    'provider "google" {\n  project = var.project_id\n  region  = var.region\n}\n\n'
    'data "google_project" "p" {}\n\noutput "numero" {\n  value = data.google_project.p.number\n}\n'
)
_AYUDAS_GCP = [
    ("could not find default credentials", "ejecute `gcloud auth application-default login` en el equipo"),
    ("getaccesstoken", "la cuenta que inició sesión necesita roles/iam.serviceAccountTokenCreator sobre la cuenta suplantada"),
    ("has not been used in project", "habilite en el proyecto la API que indica el mensaje"),
    ("invalid_grant", "la sesión de gcloud venció: repita `gcloud auth application-default login`"),
    ("403", "la cuenta suplantada no tiene permiso de lectura en el proyecto (rol Viewer)"),
]


def _ayuda_gcp(salida: str) -> str:
    s = salida.lower()
    return next((a for clave, a in _AYUDAS_GCP if clave in s), "revise el mensaje de error")


def _probar_cuenta(reg: Registro, cfg: Config, cuenta: str, etiqueta: str, *, aviso: bool = False) -> None:
    env = _entorno_base(cfg.proyecto_gcp, cfg.region, cuenta)
    with tempfile.TemporaryDirectory(prefix="diag-") as tmp:
        d = Path(tmp)
        (d / "main.tf").write_text(_PLANTILLA_DIAGNOSTICO, encoding="utf-8")
        p = _terraform(["init", "-input=false", "-no-color"], d, env)
        if p.returncode == 0:
            p = _terraform(["plan", "-input=false", "-lock=false", "-no-color"], d, env)
        salida = p.stdout + p.stderr
        reg.comprobar(f"terraform plan suplantando la {etiqueta} ({cuenta})",
                      "lee el proyecto (credencial, suplantación y API funcionan)",
                      "correcto" if p.returncode == 0 else f"código {p.returncode}. {_resumen(salida, 3)}",
                      p.returncode == 0, aviso=aviso, ayuda=_ayuda_gcp(salida))


def diagnostico() -> int:
    """Revisa el entorno antes de S5, de la corrida del experimento o del despliegue. No muestra secretos."""
    cfg = Config.desde_entorno()
    reg = Registro("diagnostico", "Diagnóstico del entorno del sandbox")

    clave = os.environ.get("GEMINI_API_KEY", "")
    reg.comprobar("GEMINI_API_KEY", "definida", f"definida ({len(clave)} caracteres)" if clave else "vacía",
                  bool(clave), ayuda="complete GEMINI_API_KEY en .env")
    for nombre, valor, obligatoria in [("AGENT_MODELO", cfg.modelo, True), ("GCP_PROYECTO", cfg.proyecto_gcp, True),
                                       ("GCP_REGION", cfg.region, True),
                                       ("GCP_CUENTA_EVALUACION", cfg.cuenta_evaluacion, True),
                                       ("GCP_CUENTA_DESPLIEGUE", cfg.cuenta_despliegue, False)]:
        reg.comprobar(nombre, "definida", valor or "vacía", bool(valor), aviso=not obligatoria,
                      ayuda=f"complete {nombre} en .env" + ("" if obligatoria else " (solo para el modo despliegue)"))

    ruta = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
    try:
        if not ruta:
            raise FileNotFoundError("GOOGLE_APPLICATION_CREDENTIALS no está definida")
        tipo = json.loads(Path(ruta).read_text(encoding="utf-8")).get("type", "?")
        obtenido, ok = f"archivo legible, tipo {tipo}", tipo in ("authorized_user", "service_account",
                                                                 "impersonated_service_account", "external_account")
    except (OSError, ValueError) as e:
        obtenido, ok = f"no se pudo leer ({e if not ruta else type(e).__name__})", False
    reg.comprobar(f"Credencial de gcloud montada en {ruta or '(sin GOOGLE_APPLICATION_CREDENTIALS)'}",
                  "archivo de application_default_credentials", obtenido, ok,
                  ayuda="ejecute `gcloud auth application-default login` (en Linux o macOS, defina ADC_HOST en .env)")
    reg.escribir()

    obtenido, codigo = _https("https://www.googleapis.com/discovery/v1/apis?name=storage&fields=kind")
    reg.comprobar("Salida a las APIs de Google por el proxy", "responde el destino", obtenido, codigo is not None,
                  ayuda="levante el proxy con `docker compose up -d proxy` y revise `docker compose logs proxy`")

    if clave:
        modelo = cfg.modelo.split("/", 1)[-1]
        req = urllib.request.Request(f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}",
                                     headers={"x-goog-api-key": clave})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                obtenido, ok = f"HTTP {r.status}: el modelo existe y la clave es válida", True
        except urllib.error.HTTPError as e:
            motivo = {400: "clave inválida", 403: "clave sin permiso", 404: "el modelo no existe"}.get(e.code, "")
            obtenido, ok = f"HTTP {e.code} {motivo}".strip(), False
        except OSError as e:   # incluye URLError: sin respuesta del destino
            obtenido, ok = f"sin respuesta: {getattr(e, 'reason', e)}", False
        reg.comprobar(f"Gemini: modelo {modelo}", "HTTP 200", obtenido, ok,
                      ayuda="revise GEMINI_API_KEY y AGENT_MODELO en .env")
    reg.escribir()

    if cfg.proyecto_gcp and cfg.cuenta_evaluacion:
        _probar_cuenta(reg, cfg, cfg.cuenta_evaluacion, "cuenta de evaluación (Viewer)")
    if cfg.proyecto_gcp and cfg.cuenta_despliegue:
        _probar_cuenta(reg, cfg, cfg.cuenta_despliegue, "cuenta de despliegue", aviso=True)
    reg.escribir()

    # Tiempo de una validación completa sin plan, para estimar la duración de la corrida.
    referencia = Path("tests/datos/caso_real")
    if referencia.exists():
        from sandbox.local_runner import ValidadorLocal
        archivos = {p.name: p.read_text(encoding="utf-8") for p in sorted(referencia.glob("*.tf"))}
        v = ValidadorLocal(None, cfg.region, None, checkov_bloqueantes=set(cfg.checkov_bloqueantes),
                           tflint_config=cfg.tflint_config, ejecutar_plan=False)
        res = v.validar(archivos)
        tiempos = ", ".join(f"{c.control} {c.duracion_s:.1f}s" for c in res.controles if not c.omitido)
        total = sum(c.duracion_s for c in res.controles)
        reg.comprobar("Validación de referencia (caso real, sin plan)", "pasan todos los controles",
                      f"{'correcto' if res.ok else 'falla en ' + res.primer_fallo.control}: {tiempos}; total {total:.1f}s",
                      res.ok)
    return reg.cerrar()


def main(argv: list[str]) -> int:
    comandos = {"diagnostico": diagnostico, "s5": s5, "s6": s6, "versiones": versiones}
    if len(argv) != 1 or argv[0] not in comandos:
        print(__doc__)
        return 2
    return comandos[argv[0]]()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
