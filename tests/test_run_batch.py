import csv
from collections import Counter
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent.schemas import ResultadoCorrida
from eval.run_batch import app, cargar_escenarios, correr_lote, planificar

ESCENARIOS = Path(__file__).resolve().parents[1] / "eval" / "scenarios"


class CorredorFalso:
    """Imita Agente.correr sin LLM ni Terraform; puede fallar en las claves indicadas."""

    def __init__(self, fallar_en=()):
        self.fallar_en = set(fallar_en)
        self.llamadas = []

    def correr(self, escenario, tratamiento="T1", *, repeticion=1, semilla=None):
        clave = (escenario.id, tratamiento, repeticion)
        self.llamadas.append(clave)
        if clave in self.fallar_en:
            raise RuntimeError("falla inesperada")
        return ResultadoCorrida(run_id=f"{len(self.llamadas):012d}", escenario_id=escenario.id,
                                nivel=escenario.nivel, tratamiento=tratamiento, repeticion=repeticion,
                                semilla=semilla, modelo="falso", validate_ok=True)


def _filas(path):
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_escenarios_definitivos():
    esc = cargar_escenarios(ESCENARIOS)
    assert Counter(e.nivel for e in esc) == {"basico": 4, "intermedio": 5, "avanzado": 3}


def test_escenario_inexistente():
    with pytest.raises(ValueError, match="XXX-99"):
        cargar_escenarios(ESCENARIOS, ["BAS-01", "XXX-99"])


def test_plan_completo_y_reproducible():
    esc = cargar_escenarios(ESCENARIOS)
    corridas = planificar(esc, 3, semilla=7)
    claves = [c.clave for c in corridas]
    assert len(claves) == len(set(claves)) == 12 * 2 * 3
    assert claves == [c.clave for c in planificar(list(reversed(esc)), 3, semilla=7)]
    assert claves != [c.clave for c in planificar(esc, 3, semilla=8)]
    # El orden queda mezclado: no corre todo T0 antes que T1
    assert {c.tratamiento for c in corridas[:12]} == {"T0", "T1"}


def test_lote_escribe_csv_y_reanuda(tmp_path):
    corridas = planificar(cargar_escenarios(ESCENARIOS, ["BAS-01", "INT-01"]), 3, semilla=1)
    csv_path = tmp_path / "runs.csv"
    falso = CorredorFalso(fallar_en=[("INT-01", "T1", 2)])

    assert correr_lote(falso, corridas, csv_path, semilla=1, avisar=lambda *_: None) == (11, 0, 1)
    filas = _filas(csv_path)
    assert len(filas) == 11
    assert [(f["escenario_id"], f["tratamiento"], int(f["repeticion"])) for f in filas] == \
        [c.clave for c in corridas if c.clave != ("INT-01", "T1", 2)]
    assert {f["semilla"] for f in filas} == {"1"}

    # Al reanudar solo se corre la que falló, y el encabezado no se repite
    otro = CorredorFalso()
    assert correr_lote(otro, corridas, csv_path, semilla=1, avisar=lambda *_: None) == (1, 11, 0)
    assert otro.llamadas == [("INT-01", "T1", 2)]
    assert len(_filas(csv_path)) == 12


def test_cli_solo_orden():
    r = CliRunner().invoke(app, ["--escenarios", str(ESCENARIOS), "--solo", "BAS-01", "--repeticiones", "2",
                                 "--solo-orden"])
    assert r.exit_code == 0, r.output
    assert len(r.output.strip().splitlines()) == 4


def test_cli_no_sobrescribe_csv(tmp_path):
    (tmp_path / "runs.csv").write_text("run_id\n", encoding="utf-8")
    r = CliRunner().invoke(app, ["--escenarios", str(ESCENARIOS), "--salida", str(tmp_path)])
    assert r.exit_code != 0
    assert "--reanudar" in r.output
