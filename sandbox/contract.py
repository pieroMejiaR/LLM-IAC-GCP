"""Contrato entre el agente y el sandbox.

El agente solo conoce estas clases. La implementación real del sandbox
(Docker, allowlist, credenciales) debe respetar estas firmas para que el
bucle de generación, validación y reparación funcione sin cambios.
"""
from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, Field

Control = Literal[
    "formato",   # la respuesta del LLM no trae archivos válidos
    "guard",     # revisión estática previa a init
    "fmt",
    "init",
    "validate",
    "tflint",
    "checkov",
    "plan",
    "apply",
    "destroy",
    "http",
]


class Diagnostico(BaseModel):
    """Un error o advertencia normalizado, venga de la herramienta que venga."""

    control: Control
    severidad: Literal["error", "warning"] = "error"
    mensaje: str
    archivo: str | None = None
    linea: int | None = None
    regla: str | None = None          # p. ej. CKV_GCP_2 o una regla de TFLint
    tipo_recurso: str | None = None   # p. ej. google_storage_bucket
    origen: Literal["codigo", "entorno"] = "codigo"

    def texto(self) -> str:
        ubicacion = ""
        if self.archivo:
            ubicacion = f" {self.archivo}" + (f":{self.linea}" if self.linea else "")
        regla = f" [{self.regla}]" if self.regla else ""
        return f"({self.control}){ubicacion}{regla} {self.mensaje}".strip()


class ResultadoControl(BaseModel):
    control: Control
    ok: bool
    omitido: bool = False
    diagnosticos: list[Diagnostico] = Field(default_factory=list)
    duracion_s: float = 0.0
    salida: str = ""  # salida cruda, truncada

    @property
    def errores(self) -> list[Diagnostico]:
        return [d for d in self.diagnosticos if d.severidad == "error"]

    @property
    def es_entorno(self) -> bool:
        errores = self.errores
        return bool(errores) and all(d.origen == "entorno" for d in errores)


class CambioRecurso(BaseModel):
    direccion: str            # p. ej. google_storage_bucket.sitio
    tipo: str                 # p. ej. google_storage_bucket
    acciones: list[str]       # create, update, delete, read, no-op


class ResultadoValidacion(BaseModel):
    archivos: dict[str, str]                      # archivos tras terraform fmt
    controles: list[ResultadoControl] = Field(default_factory=list)
    cambios_plan: list[CambioRecurso] = Field(default_factory=list)
    versiones: dict[str, str] = Field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.controles) and all(c.ok for c in self.controles)

    @property
    def primer_fallo(self) -> ResultadoControl | None:
        return next((c for c in self.controles if not c.ok), None)

    def control(self, nombre: str) -> ResultadoControl | None:
        return next((c for c in self.controles if c.control == nombre), None)

    @property
    def validate_ok(self) -> bool:
        c = self.control("validate")
        return bool(c and c.ok and not c.omitido)

    @property
    def plan_ok(self) -> bool:
        c = self.control("plan")
        return bool(c and c.ok and not c.omitido)

    @property
    def checkov_fallidas(self) -> int:
        c = self.control("checkov")
        return len(c.diagnosticos) if c else 0


class Validador(Protocol):
    """Modo evaluación: nunca crea, modifica ni destruye recursos."""

    def validar(self, archivos: dict[str, str]) -> ResultadoValidacion: ...


class PlanDespliegue(BaseModel):
    directorio: str
    archivo_plan: str
    cambios: list[CambioRecurso] = Field(default_factory=list)
    texto: str = ""
    controles: list[ResultadoControl] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.controles) and all(c.ok for c in self.controles)


class Desplegador(Protocol):
    """Modo despliegue: solo lo usa agent.deploy, tras la aprobación humana."""

    def planificar(self, archivos: dict[str, str]) -> PlanDespliegue: ...

    def aplicar(self, plan: PlanDespliegue) -> ResultadoControl: ...

    def salidas(self, directorio: str) -> dict: ...

    def destruir(self, directorio: str) -> ResultadoControl: ...
