"""Modelos de datos del agente: escenario, especificación, plan y resultado de corrida."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator

from sandbox.contract import Diagnostico, ResultadoValidacion

TipoServicio = Literal["cloud_run", "cloud_sql_postgres", "cloud_storage", "vpc", "secret_manager", "iam"]
Tratamiento = Literal["T0", "T1"]


class Escenario(BaseModel):
    id: str
    nivel: Literal["basico", "intermedio", "avanzado", "piloto", "caso_real", "interactivo"]
    titulo: str
    requerimiento: str
    contexto: dict = Field(default_factory=dict)
    servicios_esperados: list[str] = Field(default_factory=list)   # tipos de recurso de Terraform
    restricciones: list[str] = Field(default_factory=list)
    checklist: list[str] = Field(default_factory=list)

    @classmethod
    def desde_yaml(cls, ruta: str | Path) -> "Escenario":
        return cls(**yaml.safe_load(Path(ruta).read_text(encoding="utf-8")))


class Servicio(BaseModel):
    tipo: TipoServicio
    nombre: str = Field(pattern=r"^[a-z][a-z0-9-]{0,39}$")
    publico: bool = False
    parametros: dict[str, str | int | float | bool] = Field(default_factory=dict)


class Especificacion(BaseModel):
    proyecto: str = "proyecto"
    region: str = "southamerica-west1"
    entornos: list[str] = Field(default_factory=lambda: ["dev"])
    servicios: list[Servicio] = Field(min_length=1)
    restricciones: list[str] = Field(default_factory=list)
    supuestos: list[str] = Field(default_factory=list)

    @field_validator("servicios")
    @classmethod
    def _nombres_unicos(cls, v: list[Servicio]) -> list[Servicio]:
        claves = [(s.tipo, s.nombre) for s in v]
        if len(claves) != len(set(claves)):
            raise ValueError("Hay servicios repetidos con el mismo tipo y nombre")
        return v


class PlanArchivos(BaseModel):
    tipos_recurso: list[str]
    archivos: list[str]
    por_servicio: dict[str, list[str]]
    servicios_implicitos: list[str] = Field(default_factory=list)


class LlamadaLLM(BaseModel):
    etapa: str
    tokens_entrada: int = 0
    tokens_salida: int = 0
    tokens_razonamiento: int | None = None
    costo_usd: float = 0.0
    duracion_s: float = 0.0


class Intento(BaseModel):
    numero: int                      # 0 = generación inicial
    accion: Literal["generar", "reparar"]
    ok: bool
    control_fallido: str | None = None
    errores: list[str] = Field(default_factory=list)
    duracion_validacion_s: float = 0.0


class ResultadoCorrida(BaseModel):
    run_id: str
    escenario_id: str
    nivel: str
    tratamiento: Tratamiento
    repeticion: int = 1
    semilla: int | None = None
    modelo: str
    inicio: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    tiempo_s: float = 0.0

    especificacion: Especificacion | None = None
    plan_archivos: PlanArchivos | None = None
    archivos: dict[str, str] = Field(default_factory=dict)
    validacion: ResultadoValidacion | None = None
    intentos_detalle: list[Intento] = Field(default_factory=list)
    llamadas: list[LlamadaLLM] = Field(default_factory=list)
    errores_analisis: list[str] = Field(default_factory=list)   # respuestas de analizar que no cumplieron el esquema

    # Campos del formato de registro (Anexo C)
    validate_ok: bool = False
    plan_ok: bool = False
    intentos: int = 0
    checkov_fallidas: int = 0
    tipos_recurso_codigo: list[str] = Field(default_factory=list)
    tipos_recurso_plan: list[str] = Field(default_factory=list)
    cobertura_codigo: float | None = None
    cobertura_plan: float | None = None
    tokens_entrada: int = 0
    tokens_salida: int = 0
    costo_usd: float = 0.0
    error_final: str | None = None
    tipo_error: Literal["ninguno", "codigo", "entorno"] = "ninguno"
    versiones: dict[str, str] = Field(default_factory=dict)

    def estado_plan(self) -> str:
        """ok, omitido (sin GCP o con --sin-plan) o falla."""
        if self.plan_ok:
            return "ok"
        c = self.validacion.control("plan") if self.validacion else None
        return "omitido" if c and c.omitido else "falla"

    def fila_csv(self) -> dict:
        """Fila para results/runs.csv (Anexo C)."""
        return {
            "run_id": self.run_id, "escenario_id": self.escenario_id, "nivel": self.nivel,
            "tratamiento": self.tratamiento, "repeticion": self.repeticion, "semilla": self.semilla,
            "modelo": self.modelo, "version_terraform": self.versiones.get("terraform", ""),
            "version_proveedor": self.versiones.get("google", ""),
            "validate_ok": self.validate_ok, "plan_ok": self.plan_ok, "intentos": self.intentos,
            "checkov_fallidas": self.checkov_fallidas, "cobertura": self.cobertura_plan,
            "tiempo_s": round(self.tiempo_s, 3), "tokens_entrada": self.tokens_entrada,
            "tokens_salida": self.tokens_salida, "costo_usd": round(self.costo_usd, 6),
            "error_final": (self.error_final or "")[:300], "tipo_error": self.tipo_error,
        }


def errores_texto(diags: list[Diagnostico], maximo: int = 10) -> list[str]:
    return [d.texto() for d in diags if d.severidad == "error"][:maximo]
