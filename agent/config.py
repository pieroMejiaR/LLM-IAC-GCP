"""Configuración leída de variables de entorno (y de un archivo .env si existe)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def cargar_env(ruta: str | Path = ".env") -> None:
    """Carga KEY=VALUE de un .env sin sobrescribir variables ya definidas."""
    p = Path(ruta)
    if not p.exists():
        return
    for linea in p.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, valor = linea.split("=", 1)
        valor = valor.split(" #", 1)[0].strip().strip('"').strip("'")
        os.environ.setdefault(clave.strip(), valor)


def _opcional(nombre: str) -> str | None:
    v = os.environ.get(nombre, "").strip()
    return v or None


@dataclass(frozen=True)
class Config:
    modelo: str = "gemini/gemini-3.8-flash"
    tarifa_entrada_usd_m: float = 0.75      # USD por millón de tokens de entrada
    tarifa_salida_usd_m: float = 3.75       # USD por millón de tokens de salida (incluye razonamiento)
    max_tokens: int = 32_000
    temperatura: float | None = None        # None = valor por defecto del proveedor
    max_reparaciones: int = 5
    proyecto_gcp: str | None = None
    region: str = "southamerica-west1"
    cuenta_evaluacion: str | None = None    # cuenta de servicio con rol Viewer
    cuenta_despliegue: str | None = None    # cuenta con roles limitados al caso desplegado
    esquema_proveedor: str | None = "grounding/esquema_catalogo.json"
    version_proveedor: str = ">= 6.0"
    checkov_bloqueantes: tuple[str, ...] = ("CKV_GCP_2", "CKV_GCP_3")
    tflint_config: str | None = None

    @classmethod
    def desde_entorno(cls) -> "Config":
        cargar_env()
        temp = _opcional("AGENT_TEMPERATURA")
        bloq = _opcional("CHECKOV_BLOQUEANTES")
        return cls(
            modelo=_opcional("AGENT_MODELO") or cls.modelo,
            tarifa_entrada_usd_m=float(_opcional("TARIFA_ENTRADA_USD_M") or cls.tarifa_entrada_usd_m),
            tarifa_salida_usd_m=float(_opcional("TARIFA_SALIDA_USD_M") or cls.tarifa_salida_usd_m),
            max_tokens=int(_opcional("AGENT_MAX_TOKENS") or cls.max_tokens),
            temperatura=float(temp) if temp else None,
            max_reparaciones=int(_opcional("MAX_REPARACIONES") or cls.max_reparaciones),
            proyecto_gcp=_opcional("GCP_PROYECTO"),
            region=_opcional("GCP_REGION") or cls.region,
            cuenta_evaluacion=_opcional("GCP_CUENTA_EVALUACION"),
            cuenta_despliegue=_opcional("GCP_CUENTA_DESPLIEGUE"),
            esquema_proveedor=_opcional("ESQUEMA_PROVEEDOR") or cls.esquema_proveedor,
            version_proveedor=_opcional("VERSION_PROVEEDOR") or cls.version_proveedor,
            checkov_bloqueantes=tuple(x.strip() for x in bloq.split(",") if x.strip()) if bloq else cls.checkov_bloqueantes,
            tflint_config=_opcional("TFLINT_CONFIG"),
        )
