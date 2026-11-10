"""Lectura de runs.csv y métricas por corrida, por escenario y por tratamiento.

Unidades de análisis:
- Corrida: una fila de runs.csv (escenario × tratamiento × repetición).
- Par de corridas: T0 y T1 del mismo escenario y la misma repetición (McNemar).
- Escenario: promedio de sus repeticiones en cada tratamiento (Wilcoxon y bootstrap).

Las corridas con tipo_error = "entorno" (fallas del proveedor del LLM, de red o
de cuota) no dicen nada sobre el tratamiento y se excluyen por defecto.
"""
from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from statistics import mean

TRATAMIENTOS = ("T0", "T1")
NIVELES = ("basico", "intermedio", "avanzado")


@dataclass(frozen=True)
class Metrica:
    nombre: str
    etiqueta: str
    binaria: bool
    mayor_es_mejor: bool
    unidad: str = ""


# La cobertura del CSV es la del plan y viene vacía cuando el plan falla. Se usa
# como "cobertura efectiva": una corrida sin plan válido no cubre ningún recurso.
METRICAS: dict[str, Metrica] = {m.nombre: m for m in [
    Metrica("exito_validacion", "Éxito de validate (S_v)", binaria=True, mayor_es_mejor=True),
    Metrica("exito_plan", "Éxito de plan (S_p)", binaria=True, mayor_es_mejor=True),
    Metrica("cobertura", "Cobertura efectiva de recursos", binaria=False, mayor_es_mejor=True),
    Metrica("checkov_fallidas", "Hallazgos de Checkov", binaria=False, mayor_es_mejor=False),
    Metrica("intentos", "Reparaciones (N_i)", binaria=False, mayor_es_mejor=False),
    Metrica("tiempo_s", "Tiempo (T_e)", binaria=False, mayor_es_mejor=False, unidad="s"),
    Metrica("costo_usd", "Costo (C_i)", binaria=False, mayor_es_mejor=False, unidad="USD"),
    Metrica("tokens", "Tokens (entrada + salida)", binaria=False, mayor_es_mejor=False),
]}


def _bool(v: str) -> bool:
    return v.strip().lower() in ("true", "1", "si", "sí")


def _float(v: str) -> float | None:
    v = (v or "").strip()
    return float(v) if v else None


@dataclass(frozen=True)
class Corrida:
    escenario_id: str
    nivel: str
    tratamiento: str
    repeticion: int
    validate_ok: bool
    plan_ok: bool
    intentos: int
    checkov_fallidas: int
    cobertura_plan: float | None
    tiempo_s: float
    tokens_entrada: int
    tokens_salida: int
    costo_usd: float
    tipo_error: str

    @property
    def clave(self) -> tuple[str, str, int]:
        return (self.escenario_id, self.tratamiento, self.repeticion)

    def valor(self, metrica: str) -> float:
        if metrica == "exito_validacion":
            return float(self.validate_ok)
        if metrica == "exito_plan":
            return float(self.plan_ok)
        if metrica == "cobertura":
            return self.cobertura_plan if (self.plan_ok and self.cobertura_plan is not None) else 0.0
        if metrica == "tokens":
            return float(self.tokens_entrada + self.tokens_salida)
        if metrica in ("checkov_fallidas", "intentos", "tiempo_s", "costo_usd"):
            return float(getattr(self, metrica))
        raise KeyError(f"Métrica desconocida: {metrica}")

    @classmethod
    def desde_fila(cls, f: dict[str, str]) -> "Corrida":
        return cls(
            escenario_id=f["escenario_id"], nivel=f["nivel"], tratamiento=f["tratamiento"],
            repeticion=int(f["repeticion"]), validate_ok=_bool(f["validate_ok"]), plan_ok=_bool(f["plan_ok"]),
            intentos=int(f["intentos"] or 0), checkov_fallidas=int(f["checkov_fallidas"] or 0),
            cobertura_plan=_float(f["cobertura"]), tiempo_s=float(f["tiempo_s"] or 0),
            tokens_entrada=int(f["tokens_entrada"] or 0), tokens_salida=int(f["tokens_salida"] or 0),
            costo_usd=float(f["costo_usd"] or 0), tipo_error=f.get("tipo_error") or "ninguno",
        )


@dataclass
class Datos:
    corridas: list[Corrida]          # las que entran al análisis
    excluidas: list[Corrida]         # errores de entorno, si se excluyen
    duplicadas: int                  # filas repetidas de la misma corrida (se usa la última)


def leer_runs(ruta: Path | str, *, excluir_entorno: bool = True) -> Datos:
    """Lee runs.csv. Si una corrida aparece más de una vez (se repitió), vale la última fila."""
    with Path(ruta).open(encoding="utf-8", newline="") as f:
        filas = [Corrida.desde_fila(x) for x in csv.DictReader(f)]
    ultimas: dict[tuple[str, str, int], Corrida] = {}
    for c in filas:
        ultimas[c.clave] = c
    todas = sorted(ultimas.values(), key=lambda c: c.clave)
    if excluir_entorno:
        validas = [c for c in todas if c.tipo_error != "entorno"]
        excluidas = [c for c in todas if c.tipo_error == "entorno"]
    else:
        validas, excluidas = todas, []
    return Datos(validas, excluidas, len(filas) - len(ultimas))


def pares_por_corrida(corridas: list[Corrida], metrica: str) -> list[tuple[str, int, float, float]]:
    """(escenario, repetición, valor T0, valor T1) para cada repetición que tiene ambos tratamientos."""
    idx = {c.clave: c for c in corridas}
    pares = []
    for (esc, trat, rep), c0 in sorted(idx.items()):
        if trat != "T0":
            continue
        c1 = idx.get((esc, "T1", rep))
        if c1:
            pares.append((esc, rep, c0.valor(metrica), c1.valor(metrica)))
    return pares


def medias_por_escenario(corridas: list[Corrida], metrica: str) -> dict[str, tuple[float, float]]:
    """Promedio de las repeticiones por escenario: {escenario: (T0, T1)}, solo escenarios con ambos."""
    valores: dict[tuple[str, str], list[float]] = defaultdict(list)
    for c in corridas:
        valores[(c.escenario_id, c.tratamiento)].append(c.valor(metrica))
    escenarios = sorted({e for e, _ in valores})
    return {e: (mean(valores[(e, "T0")]), mean(valores[(e, "T1")]))
            for e in escenarios if valores.get((e, "T0")) and valores.get((e, "T1"))}


def niveles_de(corridas: list[Corrida]) -> dict[str, str]:
    return {c.escenario_id: c.nivel for c in corridas}


def resumen_tratamiento(corridas: list[Corrida], metrica: str, tratamiento: str,
                        nivel: str | None = None) -> tuple[float | None, float, int]:
    """(media, suma, n) de la métrica para un tratamiento y opcionalmente un nivel."""
    vals = [c.valor(metrica) for c in corridas
            if c.tratamiento == tratamiento and (nivel is None or c.nivel == nivel)]
    return (mean(vals) if vals else None, sum(vals), len(vals))
