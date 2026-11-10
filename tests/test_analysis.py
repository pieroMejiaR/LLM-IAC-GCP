import csv
import math
import random
from pathlib import Path

import pytest
from typer.testing import CliRunner

from eval.analysis import analizar, app, bootstrap, holm, mcnemar, wilcoxon
from eval.metrics import leer_runs
from eval.simulacion import simular

ESCENARIOS = Path(__file__).resolve().parents[1] / "eval" / "scenarios"


def _csv(tmp_path, filas, nombre="runs.csv"):
    p = tmp_path / nombre
    with p.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(filas[0]))
        w.writeheader()
        w.writerows(filas)
    return p


# ---------------------------------------------------------------- pruebas estadísticas


def test_wilcoxon_exacto_a_mano():
    # 5 diferencias positivas: W- = 0, P(W+ = 15) = 1/32, bilateral 2/32
    r = wilcoxon([0, 0, 0, 0, 0], [1, 2, 3, 4, 5])
    assert (r.n, r.w_mas, r.w_menos, r.metodo) == (5, 15, 0, "exacto")
    assert r.p == pytest.approx(2 / 32)
    assert r.r_biserial == 1.0


def test_wilcoxon_descarta_ceros_y_maneja_empates():
    r = wilcoxon([1, 1, 1, 1], [1, 2, 2, 0])
    assert r.ceros == 1 and r.n == 3
    assert r.w_mas == 4 and r.w_menos == 2      # |difs| = 1,1,1 -> rangos 2,2,2
    assert r.p == 1.0                           # W+ toma 0, 2, 4 o 6: 4 está en el centro


def test_wilcoxon_sin_diferencias():
    r = wilcoxon([0.5, 0.5], [0.5, 0.5])
    assert r.p is None and r.n == 0


def test_wilcoxon_coincide_con_scipy():
    stats = pytest.importorskip("scipy.stats")
    rng = random.Random(3)
    for n in (6, 12, 30):
        t0 = [rng.gauss(0, 1) for _ in range(n)]
        t1 = [x + rng.gauss(0.4, 1) for x in t0]
        nuestro = wilcoxon(t0, t1)
        ref = stats.wilcoxon(t1, t0, method="exact" if n <= 25 else "approx", correction=n > 25)
        assert nuestro.estadistico == pytest.approx(ref.statistic)
        assert nuestro.p == pytest.approx(ref.pvalue, rel=1e-6)


def test_mcnemar_a_mano():
    pares = [(True, True)] * 14 + [(False, True)] * 19 + [(False, False)] * 3
    r = mcnemar(pares)
    assert (r.a, r.b, r.c, r.d) == (14, 0, 19, 3)
    assert r.chi2 == pytest.approx(324 / 19)
    assert r.p_exacto == pytest.approx(2 * 0.5 ** 19)
    assert r.p_chi2 == pytest.approx(math.erfc(math.sqrt(324 / 19 / 2)))


def test_mcnemar_sin_discordantes():
    assert mcnemar([(True, True), (False, False)]).p_exacto == 1.0


def test_holm():
    aj = holm({"a": 0.01, "b": 0.04, "c": 0.03, "d": None})
    assert aj == {"a": pytest.approx(0.03), "b": pytest.approx(0.06), "c": pytest.approx(0.06), "d": None}


# ---------------------------------------------------------------- lectura y bootstrap


def test_leer_runs_excluye_entorno_y_usa_la_ultima_fila(tmp_path):
    filas = simular(ESCENARIOS, repeticiones=1, prob_entorno=0)
    repetida = dict(filas[0], validate_ok="True", plan_ok="True", cobertura="1.0", tipo_error="ninguno")
    entorno = dict(filas[1], tipo_error="entorno")
    p = _csv(tmp_path, [filas[0]] + [entorno] + filas[2:] + [repetida])
    datos = leer_runs(p)
    assert datos.duplicadas == 1
    assert [c.clave for c in datos.excluidas] == [(entorno["escenario_id"], entorno["tratamiento"], 1)]
    c0 = next(c for c in datos.corridas if c.clave == (repetida["escenario_id"], repetida["tratamiento"], 1))
    assert c0.plan_ok and c0.valor("cobertura") == 1.0
    assert len(leer_runs(p, excluir_entorno=False).corridas) == len(filas)


def test_cobertura_efectiva_es_cero_sin_plan(tmp_path):
    fila = simular(ESCENARIOS, repeticiones=1, prob_entorno=0)[0]
    p = _csv(tmp_path, [dict(fila, plan_ok="False", cobertura="")])
    assert leer_runs(p).corridas[0].valor("cobertura") == 0.0


def test_bootstrap_reproducible_y_contiene_la_estimacion(tmp_path):
    datos = leer_runs(_csv(tmp_path, simular(ESCENARIOS)))
    a = bootstrap(datos.corridas, "exito_validacion", b=500, semilla=1)
    b = bootstrap(datos.corridas, "exito_validacion", b=500, semilla=1)
    assert a == b
    assert a.t0_ic[0] <= a.t0 <= a.t0_ic[1]
    assert a.dif_ic[0] <= a.dif <= a.dif_ic[1]


# ---------------------------------------------------------------- datos simulados de punta a punta


def test_efecto_fuerte_se_detecta(tmp_path):
    r = analizar(leer_runs(_csv(tmp_path, simular(ESCENARIOS, efecto="fuerte"))), b=1000)
    assert r["mcnemar"]["exito_validacion"]["p_exacto"] < 0.05
    assert r["mcnemar"]["exito_plan"]["p_exacto"] < 0.05
    assert r["wilcoxon"]["cobertura"]["p"] < 0.05
    assert r["ic_bootstrap"]["exito_plan"]["dif_ic"][0] > 0
    assert r["ic_bootstrap"]["checkov_fallidas"]["dif_ic"][1] < 0


def test_efecto_nulo_no_se_detecta(tmp_path):
    r = analizar(leer_runs(_csv(tmp_path, simular(ESCENARIOS, efecto="nulo"))), b=1000)
    assert r["mcnemar"]["exito_validacion"]["p_exacto"] > 0.05
    assert r["wilcoxon"]["exito_plan"]["p"] > 0.05
    lo, hi = r["ic_bootstrap"]["exito_plan"]["dif_ic"]
    assert lo < 0 < hi


def test_cli_escribe_el_informe(tmp_path):
    p = _csv(tmp_path, simular(ESCENARIOS))
    r = CliRunner().invoke(app, [str(p), "--bootstrap", "200"])
    assert r.exit_code == 0, r.output
    md = (tmp_path / "analisis" / "analisis.md").read_text(encoding="utf-8")
    for titulo in ("Tabla 5.1", "Tabla 5.2", "Tabla 5.3", "Tabla 5.4", "AVA-03"):
        assert titulo in md
    assert (tmp_path / "analisis" / "analisis.json").exists()
    assert (tmp_path / "analisis" / "por_escenario.csv").exists()


def test_cli_rechaza_un_solo_tratamiento(tmp_path):
    filas = [f for f in simular(ESCENARIOS, repeticiones=1) if f["tratamiento"] == "T1"]
    r = CliRunner().invoke(app, [str(_csv(tmp_path, filas))])
    assert r.exit_code != 0
