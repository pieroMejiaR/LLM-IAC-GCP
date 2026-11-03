"""Modo despliegue: el agente aplica un plan solo con aprobación humana.

Controles (sección 4.2 del informe):
1. Activación explícita: solo se llega aquí con el comando `desplegar`.
2. Recursos permitidos: el plan se rechaza si trae tipos fuera del catálogo o
   acciones de destrucción o reemplazo (prueba S8).
3. Aprobación humana: la persona debe escribir el ID del proyecto (prueba S7).
4. Plan guardado: se aplica exactamente el plan aprobado.
5. Destrucción con una segunda aprobación; si se rechaza, se deja el comando
   para destruir después.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from sandbox.contract import CambioRecurso, Desplegador

ACCIONES_PERMITIDAS = {"create", "update", "no-op", "read"}


def verificar_plan(cambios: list[CambioRecurso], tipos_permitidos: set[str]) -> list[str]:
    problemas = []
    if not cambios:
        problemas.append("El plan no crea ningún recurso.")
    for c in cambios:
        if c.tipo not in tipos_permitidos:
            problemas.append(f"{c.direccion}: el tipo {c.tipo} no está en el catálogo.")
        prohibidas = set(c.acciones) - ACCIONES_PERMITIDAS
        if prohibidas:
            problemas.append(f"{c.direccion}: acción no permitida ({', '.join(sorted(prohibidas))}).")
    return problemas


def resumen_plan(cambios: list[CambioRecurso]) -> str:
    conteo: dict[str, int] = {}
    for c in cambios:
        clave = "+".join(c.acciones)
        conteo[clave] = conteo.get(clave, 0) + 1
    lineas = [f"  {'/'.join(c.acciones):<8} {c.direccion}" for c in cambios]
    totales = ", ".join(f"{v} {k}" for k, v in sorted(conteo.items()))
    return "Recursos del plan:\n" + "\n".join(lineas) + f"\nTotal: {totales}"


def buscar_url(salidas: dict) -> str | None:
    for clave in ("service_url", "url"):
        v = salidas.get(clave)
        if isinstance(v, str) and v.startswith("https://"):
            return v
    for v in salidas.values():
        if isinstance(v, str) and v.startswith("https://"):
            return v
    return None


def prueba_http(url: str, *, intentos: int = 18, espera_s: float = 10.0, timeout_s: float = 15.0) -> dict:
    """GET a la URL hasta obtener 200. Cloud Run puede tardar en propagar el acceso público."""
    ultimo: dict = {}
    for i in range(1, intentos + 1):
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(url, timeout=timeout_s) as r:  # noqa: S310 (URL del propio despliegue)
                codigo = r.status
        except urllib.error.HTTPError as e:
            codigo = e.code
        except Exception as e:  # red, DNS, TLS
            codigo = None
            ultimo = {"url": url, "codigo": None, "error": str(e), "intentos": i}
        latencia = round((time.perf_counter() - t0) * 1000)
        if codigo is not None:
            ultimo = {"url": url, "codigo": codigo, "latencia_ms": latencia, "intentos": i}
        if codigo == 200:
            ultimo["ok"] = True
            return ultimo
        if i < intentos:
            time.sleep(espera_s)
    ultimo["ok"] = False
    return ultimo


@dataclass
class ResultadoDespliegue:
    estado: str = "iniciado"     # cancelado, rechazado, fallo_plan, fallo_apply, desplegado, destruido
    motivo: str = ""
    cambios: list[dict] = field(default_factory=list)
    aprobado: bool = False
    aplicado: bool = False
    prueba_http: dict = field(default_factory=dict)
    destruido: bool = False
    directorio: str = ""
    duraciones_s: dict[str, float] = field(default_factory=dict)


def ejecutar_despliegue(
    archivos: dict[str, str],
    desplegador: Desplegador,
    *,
    proyecto_id: str,
    tipos_permitidos: set[str],
    confirmar: Callable[[str], str],
    dir_evidencia: Path,
    probar: Callable[[str], dict] = prueba_http,
) -> ResultadoDespliegue:
    dir_evidencia = Path(dir_evidencia)
    dir_evidencia.mkdir(parents=True, exist_ok=True)
    res = ResultadoDespliegue()

    def guardar(nombre: str, texto: str) -> None:
        (dir_evidencia / nombre).write_text(texto, encoding="utf-8")

    def terminar() -> ResultadoDespliegue:
        guardar("resultado_despliegue.json", json.dumps(asdict(res), indent=2, ensure_ascii=False))
        return res

    t0 = time.perf_counter()
    plan = desplegador.planificar(archivos)
    res.duraciones_s["plan"] = round(time.perf_counter() - t0, 2)
    res.directorio = plan.directorio
    res.cambios = [c.model_dump() for c in plan.cambios]
    guardar("plan.txt", plan.texto or "")
    if not plan.ok:
        fallo = next(c for c in plan.controles if not c.ok)
        res.estado, res.motivo = "fallo_plan", "; ".join(d.texto() for d in fallo.errores)[:800]
        return terminar()

    problemas = verificar_plan(plan.cambios, tipos_permitidos)
    if problemas:
        res.estado, res.motivo = "rechazado", " ".join(problemas)
        return terminar()

    respuesta = confirmar(
        f"{resumen_plan(plan.cambios)}\n\nPara aplicar este plan en GCP escriba el ID del proyecto ({proyecto_id}):")
    if (respuesta or "").strip() != proyecto_id:
        res.estado, res.motivo = "cancelado", "La confirmación no coincide con el ID del proyecto; no se ejecutó apply."
        return terminar()
    res.aprobado = True

    t0 = time.perf_counter()
    apply = desplegador.aplicar(plan)
    res.duraciones_s["apply"] = round(time.perf_counter() - t0, 2)
    guardar("apply.log", apply.salida)
    res.aplicado = apply.ok
    if apply.ok:
        url = buscar_url(desplegador.salidas(plan.directorio))
        if url:
            t0 = time.perf_counter()
            res.prueba_http = probar(url)
            res.duraciones_s["prueba_http"] = round(time.perf_counter() - t0, 2)
        else:
            res.prueba_http = {"ok": False, "error": "No hay una salida service_url con una URL https"}
        guardar("http.txt", json.dumps(res.prueba_http, indent=2))
        res.estado = "desplegado"
    else:
        res.estado = "fallo_apply"
        res.motivo = "; ".join(d.texto() for d in apply.errores)[:800]

    respuesta = confirmar(
        f"Para destruir ahora los recursos creados escriba otra vez el ID del proyecto ({proyecto_id}); "
        "deje vacío para conservarlos:")
    if (respuesta or "").strip() == proyecto_id:
        t0 = time.perf_counter()
        destroy = desplegador.destruir(plan.directorio)
        res.duraciones_s["destroy"] = round(time.perf_counter() - t0, 2)
        guardar("destroy.log", destroy.salida)
        res.destruido = destroy.ok
        if destroy.ok:
            res.estado = "destruido" if res.estado == "desplegado" else res.estado
    else:
        res.motivo = (res.motivo + " " if res.motivo else "") + (
            f"Recursos conservados. Para destruirlos: python -m agent destruir {plan.directorio}")
    return terminar()
