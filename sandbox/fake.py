"""Validador y desplegador simulados para pruebas y demostraciones sin Terraform."""
from __future__ import annotations

from collections.abc import Callable

from sandbox.contract import (
    CambioRecurso,
    Diagnostico,
    PlanDespliegue,
    ResultadoControl,
    ResultadoValidacion,
)


def resultado_ok(archivos: dict[str, str], tipos: list[str] | None = None) -> ResultadoValidacion:
    controles = [ResultadoControl(control=c, ok=True) for c in ("guard", "fmt", "init", "validate", "tflint", "checkov", "plan")]
    cambios = [CambioRecurso(direccion=f"{t}.r{i}", tipo=t, acciones=["create"]) for i, t in enumerate(tipos or [])]
    return ResultadoValidacion(archivos=archivos, controles=controles, cambios_plan=cambios)


def resultado_error(
    archivos: dict[str, str],
    control: str,
    mensaje: str,
    *,
    archivo: str | None = "main.tf",
    linea: int | None = None,
    tipo_recurso: str | None = None,
    origen: str = "codigo",
) -> ResultadoValidacion:
    orden = ["guard", "fmt", "init", "validate", "tflint", "checkov", "plan"]
    controles = []
    for c in orden:
        if c == control:
            d = Diagnostico(control=c, mensaje=mensaje, archivo=archivo, linea=linea,
                            tipo_recurso=tipo_recurso, origen=origen)
            controles.append(ResultadoControl(control=c, ok=False, diagnosticos=[d]))
            break
        controles.append(ResultadoControl(control=c, ok=True))
    return ResultadoValidacion(archivos=archivos, controles=controles)


class ValidadorSimulado:
    """Devuelve resultados programados en orden; si no hay, aplica `regla` o aprueba todo."""

    def __init__(
        self,
        respuestas: list[Callable[[dict[str, str]], ResultadoValidacion]] | None = None,
        regla: Callable[[dict[str, str]], ResultadoValidacion] | None = None,
    ) -> None:
        self.respuestas = list(respuestas or [])
        self.regla = regla
        self.llamadas: list[dict[str, str]] = []

    def validar(self, archivos: dict[str, str]) -> ResultadoValidacion:
        self.llamadas.append(dict(archivos))
        if self.respuestas:
            return self.respuestas.pop(0)(archivos)
        if self.regla:
            return self.regla(archivos)
        return resultado_ok(archivos)


class DesplegadorSimulado:
    def __init__(self, cambios: list[CambioRecurso], salidas: dict | None = None,
                 apply_ok: bool = True, plan_ok: bool = True) -> None:
        self.cambios = cambios
        self._salidas = salidas or {}
        self.apply_ok = apply_ok
        self.plan_ok = plan_ok
        self.aplicado = False
        self.destruido = False

    def planificar(self, archivos: dict[str, str]) -> PlanDespliegue:
        return PlanDespliegue(directorio="/tmp/simulado", archivo_plan="plan.tfplan", cambios=self.cambios,
                              texto="Plan simulado", controles=[ResultadoControl(control="plan", ok=self.plan_ok)])

    def aplicar(self, plan: PlanDespliegue) -> ResultadoControl:
        self.aplicado = True
        return ResultadoControl(control="apply", ok=self.apply_ok, salida="Apply simulado")

    def salidas(self, directorio: str) -> dict:
        return dict(self._salidas)

    def destruir(self, directorio: str) -> ResultadoControl:
        self.destruido = True
        return ResultadoControl(control="destroy", ok=True, salida="Destroy simulado")
