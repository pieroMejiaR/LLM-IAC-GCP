"""Ejecutor de referencia sin Docker: corre Terraform, TFLint y Checkov instalados en el PATH.

Sirve para probar el agente de punta a punta mientras el sandbox en Docker
está en construcción. La versión final (Santiago) debe correr lo mismo dentro
del contenedor y conservar estas firmas.

ValidadorLocal  -> modo evaluación. Solo init, fmt, validate, plan, show.
DesplegadorLocal -> modo despliegue. Agrega apply, output y destroy; solo lo usa agent.deploy.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from sandbox import guard
from sandbox.contract import (
    CambioRecurso,
    Diagnostico,
    PlanDespliegue,
    ResultadoControl,
    ResultadoValidacion,
)

PERMITIDOS_EVALUACION = {"init", "fmt", "validate", "plan", "show", "version"}
PERMITIDOS_DESPLIEGUE = PERMITIDOS_EVALUACION | {"apply", "output", "destroy"}

_PALABRAS_ENTORNO = (
    "failed to query available provider packages",
    "could not connect",
    "connection refused",
    "timeout",
    "timed out",
    "quota",
    "rate limit",
    "oauth2",
    "could not find default credentials",
    "impersonate",
    "service_disabled",
    "has not been used in project",
    "is disabled",
    "no such host",
)
_MAX_SALIDA = 20_000


class ComandoNoPermitido(RuntimeError):
    pass


def clasificar_origen(mensaje: str) -> str:
    m = mensaje.lower()
    return "entorno" if any(p in m for p in _PALABRAS_ENTORNO) else "codigo"


# --------------------------------------------------------------------------- parsers

def parse_validate_json(texto: str) -> list[Diagnostico]:
    try:
        datos = json.loads(texto)
    except json.JSONDecodeError:
        return [Diagnostico(control="validate", mensaje=texto.strip()[:500] or "Salida de validate no legible")]
    diags = []
    for d in datos.get("diagnostics", []):
        rango = d.get("range") or {}
        mensaje = d.get("summary", "")
        if d.get("detail"):
            mensaje = f"{mensaje}: {d['detail']}"
        diags.append(Diagnostico(
            control="validate",
            severidad="error" if d.get("severity") == "error" else "warning",
            mensaje=mensaje,
            archivo=rango.get("filename"),
            linea=(rango.get("start") or {}).get("line"),
            origen=clasificar_origen(mensaje),
        ))
    return diags


def parse_tflint_json(texto: str) -> list[Diagnostico]:
    try:
        datos = json.loads(texto)
    except json.JSONDecodeError:
        return [Diagnostico(control="tflint", mensaje=texto.strip()[:500] or "Salida de TFLint no legible")]
    diags = []
    for issue in datos.get("issues", []):
        regla = issue.get("rule") or {}
        rango = issue.get("range") or {}
        diags.append(Diagnostico(
            control="tflint",
            severidad="error" if regla.get("severity") == "error" else "warning",
            mensaje=issue.get("message", ""),
            regla=regla.get("name"),
            archivo=rango.get("filename"),
            linea=(rango.get("start") or {}).get("line"),
        ))
    for err in datos.get("errors", []):
        rango = err.get("range") or {}
        mensaje = err.get("message", "")
        diags.append(Diagnostico(control="tflint", mensaje=mensaje, archivo=rango.get("filename"),
                                 linea=(rango.get("start") or {}).get("line"), origen=clasificar_origen(mensaje)))
    return diags


def parse_checkov_json(texto: str, bloqueantes: set[str]) -> tuple[list[Diagnostico], bool]:
    """Devuelve (hallazgos, ok). ok es False solo si falla una regla bloqueante."""
    try:
        datos = json.loads(texto)
    except json.JSONDecodeError:
        return [Diagnostico(control="checkov", mensaje="Salida de Checkov no legible", origen="entorno")], False
    reportes = datos if isinstance(datos, list) else [datos]
    diags = []
    for rep in reportes:
        for chk in (rep.get("results") or {}).get("failed_checks", []):
            check_id = chk.get("check_id")
            lineas = chk.get("file_line_range") or [None]
            recurso = chk.get("resource") or ""
            diags.append(Diagnostico(
                control="checkov",
                severidad="error" if check_id in bloqueantes else "warning",
                mensaje=f"{chk.get('check_name', '')} ({recurso})",
                regla=check_id,
                archivo=(chk.get("file_path") or "").lstrip("/") or None,
                linea=lineas[0],
                tipo_recurso=recurso.split(".")[0] if recurso else None,
            ))
    ok = not any(d.severidad == "error" for d in diags)
    return diags, ok


def parse_plan_json_lines(texto: str) -> list[Diagnostico]:
    """Lee la salida de `terraform plan -json` (una línea JSON por evento)."""
    diags = []
    for linea in texto.splitlines():
        try:
            evento = json.loads(linea)
        except json.JSONDecodeError:
            continue
        if evento.get("type") != "diagnostic":
            continue
        d = evento.get("diagnostic") or {}
        rango = d.get("range") or {}
        mensaje = d.get("summary", "")
        if d.get("detail"):
            mensaje = f"{mensaje}: {d['detail']}"
        diags.append(Diagnostico(
            control="plan",
            severidad="error" if d.get("severity") == "error" else "warning",
            mensaje=mensaje,
            archivo=rango.get("filename"),
            linea=(rango.get("start") or {}).get("line"),
            tipo_recurso=(d.get("address") or "").split(".")[0] or None,
            origen=clasificar_origen(mensaje),
        ))
    return diags


def parse_show_json(texto: str) -> list[CambioRecurso]:
    datos = json.loads(texto)
    return [
        CambioRecurso(direccion=rc["address"], tipo=rc["type"], acciones=(rc.get("change") or {}).get("actions", []))
        for rc in datos.get("resource_changes", [])
        if rc.get("mode", "managed") == "managed"
    ]


_RE_ERROR = re.compile(r"Error:\s*(.+)")
_RE_UBIC = re.compile(r"on (\S+?) line (\d+)")


def parse_texto_errores(texto: str, control: str) -> list[Diagnostico]:
    """Para comandos sin salida JSON (fmt, init, apply, destroy)."""
    diags = []
    bloques = re.split(r"(?=Error:)", texto)
    for b in bloques:
        m = _RE_ERROR.search(b)
        if not m:
            continue
        u = _RE_UBIC.search(b)
        mensaje = " ".join(b.split())[:600]
        diags.append(Diagnostico(control=control, mensaje=mensaje,
                                 archivo=u.group(1) if u else None,
                                 linea=int(u.group(2)) if u else None,
                                 origen=clasificar_origen(mensaje)))
    if not diags and texto.strip():
        diags.append(Diagnostico(control=control, mensaje=texto.strip()[:600], origen=clasificar_origen(texto)))
    return diags


# --------------------------------------------------------------------------- ejecución

class _Ejecutor:
    def __init__(self, permitidos: set[str], terraform: str = "terraform", timeout_s: int = 600) -> None:
        self.permitidos = permitidos
        self.terraform = terraform
        self.timeout_s = timeout_s

    def terraform_cmd(self, args: list[str], cwd: Path, env: dict[str, str]) -> tuple[int, str, str, float]:
        if not args or args[0] not in self.permitidos:
            raise ComandoNoPermitido(f"Comando de Terraform no permitido en este modo: {args[:1]}")
        return self._run([self.terraform, *args], cwd, env)

    def _run(self, cmd: list[str], cwd: Path, env: dict[str, str]) -> tuple[int, str, str, float]:
        t0 = time.perf_counter()
        p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=self.timeout_s)
        return p.returncode, p.stdout, p.stderr, time.perf_counter() - t0


def _entorno_base(proyecto: str | None, region: str, cuenta: str | None) -> dict[str, str]:
    env = dict(os.environ)
    # La credencial la decide este módulo, no el entorno de quien ejecuta.
    env.pop("GOOGLE_IMPERSONATE_SERVICE_ACCOUNT", None)
    env.update({"TF_IN_AUTOMATION": "1", "TF_INPUT": "0", "CHECKPOINT_DISABLE": "1"})
    if proyecto:
        env["TF_VAR_project_id"] = proyecto
    env["TF_VAR_region"] = region
    if cuenta:
        env["GOOGLE_IMPERSONATE_SERVICE_ACCOUNT"] = cuenta
    return env


def _escribir(directorio: Path, archivos: dict[str, str]) -> None:
    directorio.mkdir(parents=True, exist_ok=True)
    for nombre, contenido in archivos.items():
        if "/" in nombre or "\\" in nombre or nombre.startswith("."):
            raise ValueError(f"Nombre de archivo no permitido: {nombre}")
        (directorio / nombre).write_text(contenido, encoding="utf-8")


def _leer_tf(directorio: Path, nombres: list[str]) -> dict[str, str]:
    return {n: (directorio / n).read_text(encoding="utf-8") for n in nombres}


def _control(nombre: str, ok: bool, diags: list[Diagnostico], dur: float, salida: str = "",
             omitido: bool = False) -> ResultadoControl:
    return ResultadoControl(control=nombre, ok=ok, omitido=omitido, diagnosticos=diags,
                            duracion_s=round(dur, 3), salida=salida[-_MAX_SALIDA:])


class ValidadorLocal:
    """Modo evaluación. Nunca ejecuta apply ni destroy."""

    def __init__(
        self,
        proyecto: str | None,
        region: str,
        cuenta_evaluacion: str | None,
        *,
        checkov_bloqueantes: set[str] | None = None,
        tflint_config: str | None = None,
        ejecutar_plan: bool = True,
        conservar: bool = False,
    ) -> None:
        self.proyecto = proyecto
        self.region = region
        self.cuenta = cuenta_evaluacion
        self.bloqueantes = checkov_bloqueantes or set()
        self.tflint_config = tflint_config
        self.ejecutar_plan = ejecutar_plan
        self.conservar = conservar
        self.ej = _Ejecutor(PERMITIDOS_EVALUACION)

    def validar(self, archivos: dict[str, str]) -> ResultadoValidacion:
        directorio = Path(tempfile.mkdtemp(prefix="iac-eval-"))
        try:
            return self._validar(directorio, archivos)
        finally:
            if not self.conservar:
                shutil.rmtree(directorio, ignore_errors=True)

    def _validar(self, d: Path, archivos: dict[str, str]) -> ResultadoValidacion:
        res = ResultadoValidacion(archivos=dict(archivos))
        env = _entorno_base(self.proyecto, self.region, None)

        diags = guard.revisar(archivos)
        res.controles.append(_control("guard", not diags, diags, 0.0))
        if diags:
            return res

        _escribir(d, archivos)
        rc, out, err, dur = self.ej.terraform_cmd(["fmt", "-no-color", "-list=false", "."], d, env)
        res.controles.append(_control("fmt", rc == 0, parse_texto_errores(err, "fmt") if rc else [], dur, out + err))
        if rc:
            return res
        res.archivos = _leer_tf(d, list(archivos))

        rc, out, err, dur = self.ej.terraform_cmd(["init", "-input=false", "-no-color"], d, env)
        res.controles.append(_control("init", rc == 0, parse_texto_errores(err, "init") if rc else [], dur, out + err))
        if rc:
            return res

        rc, out, err, dur = self.ej.terraform_cmd(["validate", "-json", "-no-color"], d, env)
        diags = parse_validate_json(out)
        res.controles.append(_control("validate", rc == 0, diags, dur, out))
        if rc:
            return res

        res.controles.append(self._tflint(d, env))
        if not res.controles[-1].ok:
            return res
        res.controles.append(self._checkov(d, env))
        if not res.controles[-1].ok:
            return res

        if not self.ejecutar_plan:
            res.controles.append(_control("plan", True, [], 0.0, "plan desactivado", omitido=True))
            return res
        if not (self.proyecto and self.cuenta):
            msg = "plan omitido: faltan GCP_PROYECTO o GCP_CUENTA_EVALUACION (cuenta Viewer)"
            res.controles.append(_control("plan", True, [Diagnostico(control="plan", severidad="warning", mensaje=msg,
                                                                     origen="entorno")], 0.0, msg, omitido=True))
            return res

        env_plan = _entorno_base(self.proyecto, self.region, self.cuenta)
        rc, out, err, dur = self.ej.terraform_cmd(
            ["plan", "-input=false", "-lock=false", "-no-color", "-json", "-out=plan.tfplan"], d, env_plan)
        diags = parse_plan_json_lines(out)
        if rc and not diags:
            diags = parse_texto_errores(err, "plan")
        res.controles.append(_control("plan", rc == 0, diags, dur, out + err))
        if rc == 0:
            rc2, out2, _, _ = self.ej.terraform_cmd(["show", "-json", "plan.tfplan"], d, env_plan)
            if rc2 == 0:
                res.cambios_plan = parse_show_json(out2)
        return res

    def _tflint(self, d: Path, env: dict[str, str]) -> ResultadoControl:
        if not shutil.which("tflint"):
            return _control("tflint", True, [], 0.0, "tflint no instalado", omitido=True)
        cmd = ["tflint", "--format", "json", "--no-color"]
        if self.tflint_config:
            cmd += ["--config", str(Path(self.tflint_config).resolve())]
        rc, out, err, dur = self.ej._run(cmd, d, env)
        diags = parse_tflint_json(out)
        ok = rc == 0 or not any(x.severidad == "error" for x in diags)
        return _control("tflint", ok, diags, dur, out + err)

    def _checkov(self, d: Path, env: dict[str, str]) -> ResultadoControl:
        if not shutil.which("checkov"):
            return _control("checkov", True, [], 0.0, "checkov no instalado", omitido=True)
        cmd = ["checkov", "-d", ".", "-o", "json", "--framework", "terraform", "--quiet", "--compact"]
        rc, out, err, dur = self.ej._run(cmd, d, env)
        diags, ok = parse_checkov_json(out, self.bloqueantes)
        return _control("checkov", ok, diags, dur, out[-_MAX_SALIDA:])


class DesplegadorLocal:
    """Modo despliegue con la cuenta de servicio de despliegue. Lo invoca solo agent.deploy."""

    def __init__(self, proyecto: str, region: str, cuenta_despliegue: str, directorio: Path) -> None:
        if not (proyecto and cuenta_despliegue):
            raise ValueError("El modo despliegue requiere GCP_PROYECTO y GCP_CUENTA_DESPLIEGUE")
        self.proyecto = proyecto
        self.region = region
        self.cuenta = cuenta_despliegue
        self.directorio = Path(directorio)
        self.ej = _Ejecutor(PERMITIDOS_DESPLIEGUE, timeout_s=1800)

    def _env(self) -> dict[str, str]:
        return _entorno_base(self.proyecto, self.region, self.cuenta)

    def planificar(self, archivos: dict[str, str]) -> PlanDespliegue:
        d = self.directorio
        plan = PlanDespliegue(directorio=str(d), archivo_plan="plan.tfplan")
        diags = guard.revisar(archivos)
        plan.controles.append(_control("guard", not diags, diags, 0.0))
        if diags:
            return plan
        _escribir(d, archivos)
        env = self._env()
        rc, out, err, dur = self.ej.terraform_cmd(["init", "-input=false", "-no-color"], d, env)
        plan.controles.append(_control("init", rc == 0, parse_texto_errores(err, "init") if rc else [], dur, out + err))
        if rc:
            return plan
        rc, out, err, dur = self.ej.terraform_cmd(
            ["plan", "-input=false", "-no-color", "-json", "-out=plan.tfplan"], d, env)
        diags = parse_plan_json_lines(out)
        if rc and not diags:
            diags = parse_texto_errores(err, "plan")
        plan.controles.append(_control("plan", rc == 0, diags, dur, out + err))
        if rc:
            return plan
        _, out_j, _, _ = self.ej.terraform_cmd(["show", "-json", "plan.tfplan"], d, env)
        plan.cambios = parse_show_json(out_j)
        _, out_t, _, _ = self.ej.terraform_cmd(["show", "-no-color", "plan.tfplan"], d, env)
        plan.texto = out_t
        return plan

    def aplicar(self, plan: PlanDespliegue) -> ResultadoControl:
        d = Path(plan.directorio)
        # Un plan guardado se aplica tal cual: no puede incluir cambios distintos a los aprobados.
        rc, out, err, dur = self.ej.terraform_cmd(["apply", "-input=false", "-no-color", plan.archivo_plan], d, self._env())
        return _control("apply", rc == 0, parse_texto_errores(err, "apply") if rc else [], dur, out + err)

    def salidas(self, directorio: str) -> dict:
        rc, out, _, _ = self.ej.terraform_cmd(["output", "-json"], Path(directorio), self._env())
        if rc:
            return {}
        return {k: v.get("value") for k, v in json.loads(out or "{}").items()}

    def destruir(self, directorio: str) -> ResultadoControl:
        rc, out, err, dur = self.ej.terraform_cmd(
            ["destroy", "-input=false", "-no-color", "-auto-approve"], Path(directorio), self._env())
        return _control("destroy", rc == 0, parse_texto_errores(err, "destroy") if rc else [], dur, out + err)
