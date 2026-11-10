"""Análisis estadístico de runs.csv: T1 frente a T0.

- Wilcoxon de rangos con signo, pareado por escenario: cada escenario aporta un
  par (promedio de sus repeticiones en T0, en T1). Con 12 escenarios el p es
  exacto. No se calcula un Wilcoxon dentro de cada escenario: con 3 pares el
  menor p posible es 0,25, así que la tabla por escenario es descriptiva.
- McNemar para las métricas binarias (validate y plan), con los pares T0/T1 de
  la misma repetición. p exacto (binomial) y χ² con corrección de continuidad.
- IC 95 % por bootstrap por conglomerados: se remuestrean escenarios con
  reemplazo (con todas sus corridas), porque las repeticiones de un mismo
  escenario no son independientes. Percentiles 2,5 y 97,5.

Solo usa la biblioteca estándar: corre igual dentro y fuera del contenedor.

    python -m eval.analysis resultados/runs.csv                 # escribe resultados/analisis/
    python -m eval.analysis resultados/simulado/runs.csv --salida resultados/simulado/analisis
"""
from __future__ import annotations

import csv
import json
import math
import random
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import mean, median

import typer

from eval.metrics import METRICAS, NIVELES, Corrida, Datos, leer_runs, medias_por_escenario, niveles_de, \
    pares_por_corrida, resumen_tratamiento

SEMILLA = 20261105
ALFA = 0.05


# ---------------------------------------------------------------------- pruebas


@dataclass
class ResultadoWilcoxon:
    n: int                 # pares con diferencia distinta de cero
    ceros: int             # pares descartados por diferencia cero
    w_mas: float           # suma de rangos de las diferencias positivas (T1 > T0)
    w_menos: float
    estadistico: float     # min(W+, W-)
    p: float | None        # bilateral; None si no hay pares con diferencia
    metodo: str            # exacto o normal
    r_biserial: float | None   # tamaño del efecto: (W+ - W-) / (W+ + W-)
    mediana_dif: float | None  # mediana de T1 - T0 (incluye ceros)


def _rangos(valores: list[float]) -> list[float]:
    """Rangos promedio (1..n) con empates."""
    orden = sorted(range(len(valores)), key=lambda i: valores[i])
    rangos = [0.0] * len(valores)
    i = 0
    while i < len(orden):
        j = i
        while j + 1 < len(orden) and valores[orden[j + 1]] == valores[orden[i]]:
            j += 1
        for k in range(i, j + 1):
            rangos[orden[k]] = (i + j) / 2 + 1
        i = j + 1
    return rangos


def wilcoxon(t0: list[float], t1: list[float], *, max_exacto: int = 25) -> ResultadoWilcoxon:
    """Wilcoxon de rangos con signo para muestras pareadas (descarta diferencias cero).

    Exacto hasta max_exacto pares: distribución de W+ por programación dinámica
    sobre los rangos (con empates, rangos promedio; es la distribución exacta
    condicionada a esos rangos). Sobre ese tamaño, aproximación normal con
    corrección por empates y por continuidad.
    """
    if len(t0) != len(t1):
        raise ValueError("Las muestras pareadas deben tener el mismo largo")
    difs_todas = [b - a for a, b in zip(t0, t1)]
    difs = [d for d in difs_todas if abs(d) > 1e-12]
    med = median(difs_todas) if difs_todas else None
    n = len(difs)
    if n == 0:
        return ResultadoWilcoxon(0, len(difs_todas), 0.0, 0.0, 0.0, None, "sin pares", None, med)
    rangos = _rangos([abs(d) for d in difs])
    w_mas = sum(r for r, d in zip(rangos, difs) if d > 0)
    w_menos = sum(r for r, d in zip(rangos, difs) if d < 0)
    total = w_mas + w_menos
    r_bis = (w_mas - w_menos) / total

    if n <= max_exacto:
        dobles = [round(2 * r) for r in rangos]          # enteros aun con rangos .5
        dist = [0] * (sum(dobles) + 1)
        dist[0] = 1
        for d in dobles:
            for s in range(len(dist) - 1, d - 1, -1):
                dist[s] += dist[s - d]
        casos = 2 ** n
        w = round(2 * w_mas)
        p_baja = sum(dist[: w + 1]) / casos
        p_alta = sum(dist[w:]) / casos
        p = min(1.0, 2 * min(p_baja, p_alta))
        metodo = "exacto"
    else:
        media = n * (n + 1) / 4
        empates: dict[float, int] = {}
        for r in rangos:
            empates[r] = empates.get(r, 0) + 1
        var = n * (n + 1) * (2 * n + 1) / 24 - sum(t ** 3 - t for t in empates.values()) / 48
        z = (abs(w_mas - media) - 0.5) / math.sqrt(var) if var > 0 else 0.0
        p = min(1.0, math.erfc(max(z, 0.0) / math.sqrt(2)))
        metodo = "normal"
    return ResultadoWilcoxon(n, len(difs_todas) - n, w_mas, w_menos, min(w_mas, w_menos), p, metodo, r_bis, med)


@dataclass
class ResultadoMcNemar:
    a: int    # T0 éxito, T1 éxito
    b: int    # T0 éxito, T1 fracaso
    c: int    # T0 fracaso, T1 éxito
    d: int    # T0 fracaso, T1 fracaso
    p_exacto: float
    chi2: float | None
    p_chi2: float | None


def mcnemar(pares: list[tuple[bool, bool]]) -> ResultadoMcNemar:
    """pares = [(éxito T0, éxito T1)]. p exacto binomial bilateral y χ² de Edwards (gl = 1)."""
    a = sum(1 for x, y in pares if x and y)
    b = sum(1 for x, y in pares if x and not y)
    c = sum(1 for x, y in pares if not x and y)
    d = sum(1 for x, y in pares if not x and not y)
    n = b + c
    if n == 0:
        return ResultadoMcNemar(a, b, c, d, 1.0, None, None)
    k = min(b, c)
    p_exacto = min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)
    chi2 = (abs(b - c) - 1) ** 2 / n
    p_chi2 = math.erfc(math.sqrt(chi2 / 2))
    return ResultadoMcNemar(a, b, c, d, p_exacto, chi2, p_chi2)


@dataclass
class IntervaloBootstrap:
    t0: float
    t0_ic: tuple[float, float]
    t1: float
    t1_ic: tuple[float, float]
    dif: float
    dif_ic: tuple[float, float]


def _percentil(ordenados: list[float], q: float) -> float:
    """Percentil con interpolación lineal (como numpy por defecto)."""
    pos = (len(ordenados) - 1) * q
    i = math.floor(pos)
    j = min(i + 1, len(ordenados) - 1)
    return ordenados[i] + (ordenados[j] - ordenados[i]) * (pos - i)


def bootstrap(corridas: list[Corrida], metrica: str, *, b: int = 10_000, semilla: int = SEMILLA,
              nivel_confianza: float = 0.95) -> IntervaloBootstrap:
    """IC por percentiles remuestreando escenarios (conglomerados) con reemplazo.

    La estimación puntual es la media sobre todas las corridas de cada tratamiento.
    """
    grupos: dict[str, dict[str, list[float]]] = {}
    for c in corridas:
        grupos.setdefault(c.escenario_id, {"T0": [], "T1": []})[c.tratamiento].append(c.valor(metrica))
    escenarios = sorted(e for e, g in grupos.items() if g["T0"] and g["T1"])
    if not escenarios:
        raise ValueError(f"No hay escenarios con ambos tratamientos para {metrica}")

    def medias(muestra: list[str]) -> tuple[float, float]:
        v0 = [x for e in muestra for x in grupos[e]["T0"]]
        v1 = [x for e in muestra for x in grupos[e]["T1"]]
        return mean(v0), mean(v1)

    rng = random.Random(semilla)
    m0s, m1s, difs = [], [], []
    for _ in range(b):
        m0, m1 = medias(rng.choices(escenarios, k=len(escenarios)))
        m0s.append(m0)
        m1s.append(m1)
        difs.append(m1 - m0)
    cola = (1 - nivel_confianza) / 2

    def ic(xs: list[float]) -> tuple[float, float]:
        xs.sort()
        return (_percentil(xs, cola), _percentil(xs, 1 - cola))

    p0, p1 = medias(escenarios)
    return IntervaloBootstrap(p0, ic(m0s), p1, ic(m1s), p1 - p0, ic(difs))


def holm(ps: dict[str, float | None]) -> dict[str, float | None]:
    """Ajuste de Holm-Bonferroni para varias métricas."""
    validos = sorted(((p, k) for k, p in ps.items() if p is not None))
    m = len(validos)
    ajustados: dict[str, float | None] = {k: None for k in ps}
    previo = 0.0
    for i, (p, k) in enumerate(validos):
        previo = max(previo, min(1.0, (m - i) * p))
        ajustados[k] = previo
    return ajustados


# ---------------------------------------------------------------------- análisis completo


def analizar(datos: Datos, *, b: int = 10_000, semilla: int = SEMILLA) -> dict:
    cs = datos.corridas
    niveles = niveles_de(cs)

    global_ = {}
    for nombre in METRICAS:
        fila = {}
        for t in ("T0", "T1"):
            m, s, n = resumen_tratamiento(cs, nombre, t)
            fila[t] = {"media": m, "suma": s, "n": n}
            fila[t]["por_nivel"] = {}
            for nv in NIVELES:
                mn, sn, nn = resumen_tratamiento(cs, nombre, t, nv)
                fila[t]["por_nivel"][nv] = {"media": mn, "suma": sn, "n": nn}
        global_[nombre] = fila

    wil = {}
    for nombre in METRICAS:
        por_esc = medias_por_escenario(cs, nombre)
        r = wilcoxon([v[0] for v in por_esc.values()], [v[1] for v in por_esc.values()])
        wil[nombre] = asdict(r) | {"escenarios": len(por_esc)}
    ajustes = holm({k: v["p"] for k, v in wil.items()})
    for k in wil:
        wil[k]["p_holm"] = ajustes[k]

    mcn = {}
    for nombre in ("exito_validacion", "exito_plan"):
        pares = pares_por_corrida(cs, nombre)
        mcn[nombre] = asdict(mcnemar([(bool(x), bool(y)) for _, _, x, y in pares]))

    boot = {}
    for nombre in ("exito_validacion", "exito_plan", "cobertura", "checkov_fallidas", "intentos",
                   "tiempo_s", "costo_usd"):
        boot[nombre] = asdict(bootstrap(cs, nombre, b=b, semilla=semilla))

    por_escenario = []
    for esc in sorted(niveles):
        fila = {"escenario": esc, "nivel": niveles[esc]}
        for nombre in ("exito_validacion", "exito_plan", "cobertura", "checkov_fallidas", "intentos"):
            valores = medias_por_escenario([c for c in cs if c.escenario_id == esc], nombre)
            t0, t1 = valores.get(esc, (None, None))
            fila[f"{nombre}_T0"], fila[f"{nombre}_T1"] = t0, t1
        fila["corridas_T0"] = sum(1 for c in cs if c.escenario_id == esc and c.tratamiento == "T0")
        fila["corridas_T1"] = sum(1 for c in cs if c.escenario_id == esc and c.tratamiento == "T1")
        por_escenario.append(fila)

    return {
        "corridas": len(cs),
        "excluidas_entorno": [list(c.clave) for c in datos.excluidas],
        "filas_duplicadas": datos.duplicadas,
        "bootstrap": {"b": b, "semilla": semilla},
        "global": global_, "wilcoxon": wil, "mcnemar": mcn, "ic_bootstrap": boot,
        "por_escenario": por_escenario,
    }


# ---------------------------------------------------------------------- informe en Markdown


def _num(x: float | None, d: int = 2) -> str:
    return "—" if x is None else f"{x:.{d}f}"


def _pct(x: float | None, d: int = 1) -> str:
    return "—" if x is None else f"{100 * x:.{d}f} %"


def _p(p: float | None) -> str:
    if p is None:
        return "—"
    return "< 0,0001" if p < 1e-4 else f"{p:.4f}"


def _valor(nombre: str, x: float | None) -> str:
    m = METRICAS[nombre]
    if m.binaria or nombre == "cobertura":
        return _pct(x)
    if nombre == "costo_usd":
        return "—" if x is None else f"{x:.4f} USD"
    return _num(x, 1 if nombre == "tiempo_s" else 2) + (f" {m.unidad}" if m.unidad and m.unidad != "USD" else "")


def a_markdown(r: dict) -> str:
    g, L = r["global"], []
    L.append("# Resultados del experimento T0 frente a T1\n")
    L.append(f"Corridas analizadas: {r['corridas']}. Excluidas por error de entorno: {len(r['excluidas_entorno'])}"
             + (f" ({', '.join(' '.join(map(str, k)) for k in r['excluidas_entorno'])})" if r["excluidas_entorno"] else "")
             + f". Filas duplicadas descartadas: {r['filas_duplicadas']}.\n")

    L.append("## Tabla 5.1: Rendimiento global\n")
    L.append("| Métrica | T0 | T1 |")
    L.append("| :--- | :---: | :---: |")
    L.append(f"| Corridas | {g['exito_validacion']['T0']['n']} | {g['exito_validacion']['T1']['n']} |")
    for nombre, m in METRICAS.items():
        celdas = []
        for t in ("T0", "T1"):
            x = g[nombre][t]
            extra = f" ({int(x['suma'])}/{x['n']})" if m.binaria and x["n"] else ""
            celdas.append(_valor(nombre, x["media"]) + extra)
        L.append(f"| {m.etiqueta} | {celdas[0]} | {celdas[1]} |")
    for nombre in ("exito_validacion", "exito_plan"):
        for nv in NIVELES:
            celdas = []
            for t in ("T0", "T1"):
                x = g[nombre][t]["por_nivel"][nv]
                celdas.append(f"{_pct(x['media'])} ({int(x['suma'])}/{x['n']})" if x["n"] else "—")
            L.append(f"| {METRICAS[nombre].etiqueta}, {nv} | {celdas[0]} | {celdas[1]} |")
    L.append("\nLa cobertura efectiva vale 0 cuando la corrida no llega a un plan válido.\n")

    L.append("## Tabla 5.2: Wilcoxon de rangos con signo, pareado por escenario\n")
    L.append("Cada escenario aporta un par: el promedio de sus repeticiones en T0 y en T1. "
             "Se descartan los escenarios con diferencia cero. p bilateral; p Holm ajusta por las métricas de la tabla.\n")
    L.append("| Métrica | Escenarios | Pares ≠ 0 | Mediana Δ (T1 − T0) | W+ | W− | W | p | p Holm | r biserial |")
    L.append("| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |")
    for nombre, w in r["wilcoxon"].items():
        L.append(f"| {METRICAS[nombre].etiqueta} | {w['escenarios']} | {w['n']} | {_num(w['mediana_dif'], 3)} | "
                 f"{_num(w['w_mas'], 1)} | {_num(w['w_menos'], 1)} | {_num(w['estadistico'], 1)} | {_p(w['p'])} | "
                 f"{_p(w['p_holm'])} | {_num(w['r_biserial'])} |")

    L.append("\n### Detalle por escenario (descriptivo)\n")
    L.append("| Escenario | Nivel | S_v T0 | S_v T1 | S_p T0 | S_p T1 | Cobertura T0 | Cobertura T1 | Checkov T0 | Checkov T1 | Reparaciones T1 |")
    L.append("| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |")
    for f in r["por_escenario"]:
        L.append(f"| {f['escenario']} | {f['nivel']} | {_pct(f['exito_validacion_T0'], 0)} | {_pct(f['exito_validacion_T1'], 0)} | "
                 f"{_pct(f['exito_plan_T0'], 0)} | {_pct(f['exito_plan_T1'], 0)} | {_pct(f['cobertura_T0'])} | "
                 f"{_pct(f['cobertura_T1'])} | {_num(f['checkov_fallidas_T0'], 1)} | {_num(f['checkov_fallidas_T1'], 1)} | "
                 f"{_num(f['intentos_T1'], 1)} |")

    L.append("\n## Tabla 5.3: McNemar (pares T0/T1 de la misma repetición)\n")
    for nombre, m in r["mcnemar"].items():
        L.append(f"**{METRICAS[nombre].etiqueta}**\n")
        L.append("| | T1 éxito | T1 fracaso | Total |")
        L.append("| :--- | :---: | :---: | :---: |")
        L.append(f"| T0 éxito | {m['a']} | {m['b']} | {m['a'] + m['b']} |")
        L.append(f"| T0 fracaso | {m['c']} | {m['d']} | {m['c'] + m['d']} |")
        L.append(f"| Total | {m['a'] + m['c']} | {m['b'] + m['d']} | {m['a'] + m['b'] + m['c'] + m['d']} |\n")
        L.append(f"Discordantes: b = {m['b']}, c = {m['c']}. p exacto (binomial) = {_p(m['p_exacto'])}"
                 + (f"; χ² con corrección de continuidad = {m['chi2']:.2f} (gl = 1), p = {_p(m['p_chi2'])}."
                    if m["chi2"] is not None else "; sin pares discordantes.") + "\n")

    b_txt = f"{r['bootstrap']['b']:,}".replace(",", ".")
    L.append(f"## Tabla 5.4: IC 95 % por bootstrap (B = {b_txt}, por escenario, semilla {r['bootstrap']['semilla']})\n")
    L.append("| Métrica | T0 [IC 95 %] | T1 [IC 95 %] | Δ (T1 − T0) [IC 95 %] |")
    L.append("| :--- | :---: | :---: | :---: |")
    for nombre, x in r["ic_bootstrap"].items():
        def celda(v, ic):
            return f"{_valor(nombre, v)} [{_valor(nombre, ic[0])}, {_valor(nombre, ic[1])}]"
        L.append(f"| {METRICAS[nombre].etiqueta} | {celda(x['t0'], x['t0_ic'])} | {celda(x['t1'], x['t1_ic'])} | "
                 f"{celda(x['dif'], x['dif_ic'])} |")
    return "\n".join(L) + "\n"


def escribir(r: dict, salida: Path) -> None:
    salida.mkdir(parents=True, exist_ok=True)
    (salida / "analisis.json").write_text(json.dumps(r, indent=2, ensure_ascii=False), encoding="utf-8")
    (salida / "analisis.md").write_text(a_markdown(r), encoding="utf-8")
    with (salida / "por_escenario.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(r["por_escenario"][0]))
        w.writeheader()
        w.writerows(r["por_escenario"])


app = typer.Typer(add_completion=False, help="Análisis estadístico de runs.csv (T1 frente a T0).")


@app.command()
def main(
    runs: Path = typer.Argument(Path("resultados/runs.csv"), exists=True, dir_okay=False, help="runs.csv del lote"),
    salida: Path = typer.Option(None, help="Carpeta de salida (por defecto, <carpeta de runs.csv>/analisis)"),
    bootstrap_b: int = typer.Option(10_000, "--bootstrap", min=100, help="Remuestreos del bootstrap"),
    semilla: int = typer.Option(SEMILLA, help="Semilla del bootstrap"),
    incluir_entorno: bool = typer.Option(False, help="No excluir las corridas con error de entorno"),
) -> None:
    """Calcula las tablas 5.1 a 5.4 y escribe analisis.md, analisis.json y por_escenario.csv."""
    datos = leer_runs(runs, excluir_entorno=not incluir_entorno)
    if not datos.corridas:
        raise typer.BadParameter(f"{runs} no tiene corridas para analizar")
    if not any(c.tratamiento == "T0" for c in datos.corridas) or not any(c.tratamiento == "T1" for c in datos.corridas):
        raise typer.BadParameter("Hacen falta corridas de T0 y de T1 para comparar")
    r = analizar(datos, b=bootstrap_b, semilla=semilla)
    destino = salida or runs.parent / "analisis"
    escribir(r, destino)
    typer.echo(f"{r['corridas']} corridas, {len(r['excluidas_entorno'])} excluidas por entorno. "
               f"Informe en {destino / 'analisis.md'}")


if __name__ == "__main__":
    app()
