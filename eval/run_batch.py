"""Ejecución por lotes del experimento: escenarios × {T0, T1} × repeticiones.

Las corridas se ejecutan en orden aleatorio con una semilla fija, para que el
orden no confunda los tratamientos con cambios del entorno (cuota, latencia
del LLM) y para que el mismo lote se pueda repetir. Cada corrida agrega su
fila a runs.csv (formato del Anexo C) apenas termina, y deja sus archivos y su
traza en <salida>/<escenario>/<tratamiento>-<run_id>/, igual que `agent generar`.

    python -m eval.run_batch                         # 12 escenarios × 2 × 3 = 72 corridas
    python -m eval.run_batch --solo-orden            # muestra el orden sin llamar al LLM
    python -m eval.run_batch --reanudar              # salta las corridas que ya están en runs.csv
    docker compose run --rm --entrypoint python agente -m eval.run_batch
"""
from __future__ import annotations

import csv
import json
import random
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

import typer

from agent.schemas import Escenario, ResultadoCorrida, Tratamiento

TRATAMIENTOS: tuple[Tratamiento, ...] = ("T0", "T1")
SEMILLA = 20261105


class _Corredor(Protocol):
    def correr(self, escenario: Escenario, tratamiento: Tratamiento = "T1", *, repeticion: int = 1,
               semilla: int | None = None) -> ResultadoCorrida: ...


@dataclass(frozen=True)
class Corrida:
    escenario: Escenario
    tratamiento: Tratamiento
    repeticion: int

    @property
    def clave(self) -> tuple[str, str, int]:
        return (self.escenario.id, self.tratamiento, self.repeticion)

    def __str__(self) -> str:
        return f"{self.escenario.id} {self.tratamiento} rep {self.repeticion}"


def cargar_escenarios(directorio: Path, ids: list[str] | None = None) -> list[Escenario]:
    escenarios = [Escenario.desde_yaml(p) for p in sorted(directorio.glob("*.yaml"))]
    if ids:
        faltan = set(ids) - {e.id for e in escenarios}
        if faltan:
            raise ValueError(f"Escenarios no encontrados en {directorio}: {', '.join(sorted(faltan))}")
        escenarios = [e for e in escenarios if e.id in ids]
    if not escenarios:
        raise ValueError(f"No hay escenarios .yaml en {directorio}")
    repetidos = sorted({e.id for e in escenarios if [x.id for x in escenarios].count(e.id) > 1})
    if repetidos:
        raise ValueError(f"IDs de escenario repetidos: {', '.join(repetidos)}")
    return escenarios


def planificar(escenarios: list[Escenario], repeticiones: int, semilla: int,
               tratamientos: tuple[Tratamiento, ...] = TRATAMIENTOS) -> list[Corrida]:
    """Producto escenarios × tratamientos × repeticiones, barajado con la semilla.

    La lista base se ordena por ID antes de barajar, así el orden depende solo
    de la semilla y del conjunto de corridas, no del orden de lectura de archivos.
    """
    base = [Corrida(e, t, r) for e in sorted(escenarios, key=lambda e: e.id)
            for t in tratamientos for r in range(1, repeticiones + 1)]
    random.Random(semilla).shuffle(base)
    return base


def leer_hechas(csv_path: Path) -> set[tuple[str, str, int]]:
    if not csv_path.exists():
        return set()
    with csv_path.open(encoding="utf-8", newline="") as f:
        return {(fila["escenario_id"], fila["tratamiento"], int(fila["repeticion"])) for fila in csv.DictReader(f)}


def agregar_fila(csv_path: Path, fila: dict) -> None:
    nuevo = not csv_path.exists() or csv_path.stat().st_size == 0
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(fila))
        if nuevo:
            w.writeheader()
        w.writerow(fila)


def correr_lote(
    agente: _Corredor,
    corridas: list[Corrida],
    csv_path: Path,
    *,
    semilla: int,
    guardar=None,
    avisar=print,
) -> tuple[int, int, int]:
    """Ejecuta las corridas que no estén ya en csv_path. Devuelve (hechas, saltadas, fallidas).

    Una excepción no prevista en una corrida se registra y el lote sigue; esa
    corrida no se escribe en el CSV, de modo que --reanudar la vuelve a intentar.
    """
    hechas_antes = leer_hechas(csv_path)
    hechas = saltadas = fallidas = 0
    total = len(corridas)
    for i, c in enumerate(corridas, 1):
        if c.clave in hechas_antes:
            saltadas += 1
            continue
        avisar(f"[{i}/{total}] {c}")
        try:
            res = agente.correr(c.escenario, c.tratamiento, repeticion=c.repeticion, semilla=semilla)
        except Exception:  # noqa: BLE001 - un fallo inesperado no debe detener las demás corridas
            fallidas += 1
            avisar(f"    excepción no prevista, la corrida queda pendiente:\n{traceback.format_exc()}")
            continue
        if guardar:
            guardar(res)
        agregar_fila(csv_path, res.fila_csv())
        hechas += 1
        avisar(f"    validate={'ok' if res.validate_ok else 'falla'}  plan={'ok' if res.plan_ok else 'falla'}  "
               f"reparaciones={res.intentos}  tiempo={res.tiempo_s:.1f}s  costo=${res.costo_usd:.4f}"
               + (f"  error={res.tipo_error}" if res.error_final else ""))
    return hechas, saltadas, fallidas


app = typer.Typer(add_completion=False, help=__doc__.split("\n\n")[0])


@app.command()
def main(
    escenarios: Path = typer.Option(Path("eval/scenarios"), exists=True, file_okay=False,
                                    help="Carpeta con los escenarios .yaml"),
    solo: list[str] = typer.Option(None, "--solo", help="Correr solo estos IDs (repetible)"),
    repeticiones: int = typer.Option(3, min=1, help="Repeticiones por escenario y tratamiento"),
    semilla: int = typer.Option(SEMILLA, help="Semilla del orden aleatorio"),
    salida: Path = typer.Option(Path("resultados"), help="Carpeta de resultados (runs.csv y una carpeta por corrida)"),
    reanudar: bool = typer.Option(False, help="Saltar las corridas que ya están en runs.csv"),
    solo_orden: bool = typer.Option(False, help="Mostrar el orden de las corridas y salir, sin llamar al LLM"),
    simulado: bool = typer.Option(False, help="Validador simulado (sin Terraform)"),
    sin_plan: bool = typer.Option(False, help="No ejecutar terraform plan"),
) -> None:
    """Corre el lote completo y escribe <salida>/runs.csv."""
    try:
        lista = cargar_escenarios(escenarios, solo or None)
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e
    corridas = planificar(lista, repeticiones, semilla)

    if solo_orden:
        for i, c in enumerate(corridas, 1):
            typer.echo(f"{i:3}  {c}")
        return

    csv_path = salida / "runs.csv"
    if csv_path.exists() and not reanudar:
        raise typer.BadParameter(f"{csv_path} ya existe. Use --reanudar para continuar ese lote o elija otra --salida.")

    from agent.cli import _componentes, _guardar
    from agent.config import Config

    cfg = Config.desde_entorno()
    agente = _componentes(cfg, simulado, sin_plan)
    salida.mkdir(parents=True, exist_ok=True)
    (salida / f"lote-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json").write_text(json.dumps({
        "semilla": semilla, "repeticiones": repeticiones, "modelo": cfg.modelo,
        "max_reparaciones": cfg.max_reparaciones, "temperatura": cfg.temperatura,
        "simulado": simulado, "sin_plan": sin_plan, "reanudar": reanudar,
        "escenarios": [e.id for e in lista], "orden": [list(c.clave) for c in corridas],
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    hechas, saltadas, fallidas = correr_lote(agente, corridas, csv_path, semilla=semilla,
                                             guardar=lambda r: _guardar(r, salida), avisar=typer.echo)
    typer.echo(f"Listo: {hechas} corridas nuevas, {saltadas} ya estaban, {fallidas} con excepción. CSV: {csv_path}")
    if fallidas:
        typer.echo("Vuelva a ejecutar con --reanudar para reintentar las corridas con excepción.", err=True)
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
