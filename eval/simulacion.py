"""runs.csv simulado para probar eval/analysis.py sin gastar en el LLM.

Genera las mismas filas que el lote (escenarios × T0/T1 × repeticiones, con
ResultadoCorrida.fila_csv()) a partir de probabilidades por nivel. Con
--efecto nulo, T1 usa las mismas probabilidades que T0: sirve para comprobar
que el análisis no encuentra diferencias donde no las hay.

Estos datos son inventados: no se usan en el informe.

    python -m eval.simulacion                          # resultados/simulado/runs.csv, efecto fuerte
    python -m eval.simulacion --efecto nulo --salida resultados/simulado-nulo/runs.csv
    python -m eval.analysis resultados/simulado/runs.csv
"""
from __future__ import annotations

import csv
import random
from pathlib import Path

import typer

from agent.schemas import ResultadoCorrida
from eval.run_batch import SEMILLA, cargar_escenarios, planificar

# Probabilidad de pasar validate y, dado validate, de llegar a un plan válido.
_T0 = {"basico": (0.75, 0.70), "intermedio": (0.45, 0.50), "avanzado": (0.25, 0.35)}
_T1 = {"basico": (0.98, 0.95), "intermedio": (0.92, 0.90), "avanzado": (0.85, 0.80)}
_TIEMPO_T0 = {"basico": 25, "intermedio": 40, "avanzado": 60}
_CHECKOV_T0 = {"basico": 1.5, "intermedio": 3.0, "avanzado": 5.0}


def simular(escenarios_dir: Path, *, repeticiones: int = 3, semilla: int = SEMILLA, efecto: str = "fuerte",
            prob_entorno: float = 0.03) -> list[dict]:
    rng = random.Random(semilla)
    filas = []
    corridas = planificar(cargar_escenarios(escenarios_dir), repeticiones, semilla)
    for i, c in enumerate(corridas):
        nivel, t1 = c.escenario.nivel, c.tratamiento == "T1"
        mejora = t1 and efecto == "fuerte"
        p_val, p_plan = (_T1 if mejora else _T0)[nivel]
        res = ResultadoCorrida(run_id=f"sim{i:09d}", escenario_id=c.escenario.id, nivel=nivel,
                               tratamiento=c.tratamiento, repeticion=c.repeticion, semilla=semilla,
                               modelo="simulado")
        if rng.random() < prob_entorno:
            res.tipo_error, res.error_final = "entorno", "Simulado: el proveedor del LLM no respondió"
            res.tiempo_s = rng.uniform(10, 20)
            filas.append(res.fila_csv())
            continue
        res.validate_ok = rng.random() < p_val
        res.plan_ok = res.validate_ok and rng.random() < p_plan
        esperados = len(c.escenario.servicios_esperados)
        if res.plan_ok:
            faltan = 0 if mejora and rng.random() < 0.85 else rng.randint(0, max(1, esperados // 3))
            res.cobertura_plan = round((esperados - faltan) / esperados, 4)
        res.intentos = rng.choice([0, 0, 1, 1, 2, 3]) if t1 and efecto == "fuerte" else (
            rng.choice([0, 1, 2]) if t1 else 0)
        base = _CHECKOV_T0[nivel]
        res.checkov_fallidas = max(0, round(rng.gauss(base * (0.3 if mejora else 1.0), 1.0)))
        llamadas = (2 + res.intentos) if t1 else 1
        res.tiempo_s = sum(rng.uniform(0.6, 1.4) * _TIEMPO_T0[nivel] for _ in range(llamadas))
        res.tokens_entrada = llamadas * rng.randint(800, 4000)
        res.tokens_salida = llamadas * rng.randint(1000, 3500)
        res.costo_usd = (res.tokens_entrada * 0.75 + res.tokens_salida * 3.75) / 1_000_000
        if not res.plan_ok:
            res.tipo_error = "codigo"
            res.error_final = "Simulado: falló validate" if not res.validate_ok else "Simulado: falló plan"
        filas.append(res.fila_csv())
    return filas


def main(
    salida: Path = typer.Option(Path("resultados/simulado/runs.csv"), help="Archivo CSV a escribir"),
    escenarios: Path = typer.Option(Path("eval/scenarios"), exists=True, file_okay=False),
    efecto: str = typer.Option("fuerte", help="fuerte (T1 mejor que T0) o nulo (mismas probabilidades)"),
    repeticiones: int = typer.Option(3, min=1),
    semilla: int = typer.Option(SEMILLA),
) -> None:
    """Escribe un runs.csv simulado."""
    if efecto not in ("fuerte", "nulo"):
        raise typer.BadParameter("efecto debe ser fuerte o nulo")
    filas = simular(escenarios, repeticiones=repeticiones, semilla=semilla, efecto=efecto)
    salida.parent.mkdir(parents=True, exist_ok=True)
    with salida.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(filas[0]))
        w.writeheader()
        w.writerows(filas)
    typer.echo(f"{len(filas)} corridas simuladas (efecto {efecto}) en {salida}")


if __name__ == "__main__":
    typer.run(main)
