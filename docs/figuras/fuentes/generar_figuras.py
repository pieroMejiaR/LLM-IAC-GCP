"""Genera las figuras del informe en docs/figuras/ (escala de grises, sin colores).

Uso, desde la raíz del repositorio:
    python docs/figuras/fuentes/generar_figuras.py

- Diagramas: renderiza los .mmd de esta carpeta con Mermaid (tema neutral) en Edge sin interfaz.
- Gráficos: matplotlib a partir de docs/experimento/lote-v2/runs.csv y eval/manual/medicion_manual.csv.
- Capturas: recortes de docs/figuras/capturas/ (las capturas originales no se modifican).
"""
from __future__ import annotations

import csv
import json
import os
import statistics as st
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from PIL import Image, ImageChops, ImageDraw  # noqa: E402

RAIZ = Path(__file__).resolve().parents[3]
FIG = RAIZ / "docs" / "figuras"
FUENTES = FIG / "fuentes"
CAPTURAS = FIG / "capturas"

GRIS_OSCURO, GRIS_MEDIO, GRIS_CLARO, TINTA = "#3a3a3a", "#8c8c8c", "#cfcfcf", "#1a1a1a"
plt.rcParams.update({
    "font.family": "Arial", "font.size": 10, "axes.edgecolor": "#7a7a7a", "axes.linewidth": 0.8,
    "axes.labelcolor": TINTA, "xtick.color": TINTA, "ytick.color": TINTA, "text.color": TINTA,
    "axes.spines.top": False, "axes.spines.right": False, "savefig.dpi": 200,
})


# ------------------------------------------------------------------ diagramas Mermaid
EDGE = next(p for p in (Path(os.environ.get("ProgramFiles(x86)", "")) / "Microsoft/Edge/Application/msedge.exe",
                        Path(os.environ.get("ProgramFiles", "")) / "Microsoft/Edge/Application/msedge.exe") if p.exists())
PLANTILLA = """<!doctype html><html><head><meta charset="utf-8">
<style>html,body{margin:0;background:#fff} #d{display:inline-block;padding:24px}</style>
<script src="https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.min.js"></script></head>
<body><div id="d"><pre class="mermaid">__FUENTE__</pre></div>
<script>
mermaid.initialize({startOnLoad: true, theme: "neutral", securityLevel: "strict",
  themeVariables: {fontFamily: "Arial", fontSize: "18px"},
  flowchart: {useMaxWidth: false, htmlLabels: true, curve: "basis"},
  sequence: {useMaxWidth: false, mirrorActors: false, wrap: false}});
</script></body></html>"""


def recortar_blanco(ruta: Path, margen: int = 16) -> None:
    im = Image.open(ruta).convert("RGB")
    caja = ImageChops.difference(im, Image.new("RGB", im.size, "white")).getbbox()
    if caja:
        x0, y0, x1, y1 = caja
        im = im.crop((max(0, x0 - margen), max(0, y0 - margen), min(im.width, x1 + margen), min(im.height, y1 + margen)))
    im.save(ruta, optimize=True)


def mermaid(fuente: Path, destino: Path, ancho: int, alto: int) -> None:
    texto = fuente.read_text(encoding="utf-8").replace("&", "&amp;").replace("<br>", "#br#").replace("<", "&lt;").replace("#br#", "<br>")
    with tempfile.TemporaryDirectory() as tmp:
        html = Path(tmp) / "d.html"
        html.write_text(PLANTILLA.replace("__FUENTE__", texto), encoding="utf-8")
        subprocess.run([str(EDGE), "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                        f"--user-data-dir={Path(tmp) / 'perfil'}", "--force-device-scale-factor=2",
                        f"--window-size={ancho},{alto}", "--virtual-time-budget=10000",
                        f"--screenshot={destino}", html.as_uri()], check=True, capture_output=True, timeout=120)
    recortar_blanco(destino)


# ------------------------------------------------------------------ gráficos
def corridas() -> list[dict]:
    with open(RAIZ / "docs/experimento/lote-v2/runs.csv", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def grafico_tiempo_costo(destino: Path) -> dict:
    filas = corridas()
    fig, ejes = plt.subplots(1, 2, figsize=(7.2, 3.2))
    resumen = {}
    for eje, (col, titulo, fmt) in zip(ejes, [("tiempo_s", "Tiempo por corrida (s)", "{:.1f} s"),
                                             ("costo_usd", "Costo de LLM por corrida (USD)", "{:.4f}")]):
        datos = [[float(x[col]) for x in filas if x["tratamiento"] == t] for t in ("T0", "T1")]
        caja = eje.boxplot(datos, positions=[1, 2], widths=0.45, patch_artist=True, showfliers=False,
                           medianprops={"color": TINTA, "linewidth": 1.6},
                           whiskerprops={"color": GRIS_MEDIO}, capprops={"color": GRIS_MEDIO})
        for parche, relleno in zip(caja["boxes"], ("white", GRIS_CLARO)):
            parche.set(facecolor=relleno, edgecolor=GRIS_OSCURO, linewidth=1)
        for i, valores in enumerate(datos, start=1):
            n = len(valores)
            xs = [i - 0.16 + 0.32 * (k / max(1, n - 1)) for k in range(n)]
            eje.scatter(xs, sorted(valores), s=9, color=GRIS_OSCURO, alpha=0.55, zorder=3, linewidths=0)
            med = st.median(valores)
            eje.annotate("mediana " + fmt.format(med), (i + 0.26, med), va="center", fontsize=8, color=TINTA)
            resumen[(col, i)] = med
        eje.set_xticks([1, 2], ["T0 (un paso)", "T1 (agente)"])
        eje.set_xlim(0.5, 2.95)
        eje.set_title(titulo, fontsize=10, loc="left")
        eje.grid(axis="y", color="#e2e2e2", linewidth=0.6)
        eje.set_axisbelow(True)
    fig.tight_layout()
    fig.savefig(destino, facecolor="white")
    plt.close(fig)
    return resumen


def grafico_cobertura(destino: Path) -> dict:
    por = defaultdict(list)
    for x in corridas():
        por[(x["nivel"], x["tratamiento"], x["escenario_id"])].append(float(x["cobertura"] or 0))
    nivel = defaultdict(list)
    for (n, t, _), v in por.items():
        nivel[(n, t)].append(st.mean(v))
    niveles = [("basico", "Básico"), ("intermedio", "Intermedio"), ("avanzado", "Avanzado")]
    fig, eje = plt.subplots(figsize=(6.4, 3.0))
    ancho = 0.34
    valores = {}
    for j, (t, relleno, trama) in enumerate((("T0", "white", "///"), ("T1", GRIS_OSCURO, ""))):
        ys = [100 * st.mean(nivel[(n, t)]) for n, _ in niveles]
        xs = [i + (j - 0.5) * (ancho + 0.03) for i in range(len(niveles))]
        barras = eje.bar(xs, ys, ancho, color=relleno, edgecolor=GRIS_OSCURO, hatch=trama, linewidth=1,
                         label={"T0": "T0 (un paso)", "T1": "T1 (agente)"}[t])
        for b, y in zip(barras, ys):
            eje.annotate(f"{y:.1f} %", (b.get_x() + b.get_width() / 2, y + 1.2), ha="center", fontsize=8.5)
        valores[t] = ys
    eje.set_xticks(range(len(niveles)), [e for _, e in niveles])
    eje.set_ylim(0, 112)
    eje.set_yticks([0, 25, 50, 75, 100], ["0 %", "25 %", "50 %", "75 %", "100 %"])
    eje.set_ylabel("Cobertura efectiva media")
    eje.grid(axis="y", color="#e2e2e2", linewidth=0.6)
    eje.set_axisbelow(True)
    eje.legend(frameon=False, loc="upper center", bbox_to_anchor=(0.5, 1.13), ncol=2, fontsize=9)
    fig.tight_layout()
    fig.savefig(destino, facecolor="white")
    plt.close(fig)
    return valores


def grafico_manual(destino: Path) -> list:
    with open(RAIZ / "eval/manual/medicion_manual.csv", encoding="utf-8") as f:
        filas = list(csv.DictReader(f))
    etiquetas = [f"{x['escenario']}\n({x['nivel'].replace('basico', 'básico')})" for x in filas]
    manual = [float(x["tiempo_manual_seg"]) for x in filas]
    agente = [float(x["tiempo_agente_t1_seg"]) for x in filas]
    fig, eje = plt.subplots(figsize=(6.4, 3.2))
    ancho = 0.34
    for j, (vals, relleno, trama, nombre, fmt) in enumerate((
            (manual, "white", "///", "Redacción manual", lambda s: f"{s / 60:.1f} min"),
            (agente, GRIS_OSCURO, "", "Agente (T1)", lambda s: f"{s:.1f} s"))):
        xs = [i + (j - 0.5) * (ancho + 0.03) for i in range(len(vals))]
        barras = eje.bar(xs, vals, ancho, color=relleno, edgecolor=GRIS_OSCURO, hatch=trama, linewidth=1, label=nombre)
        for b, v in zip(barras, vals):
            eje.annotate(fmt(v), (b.get_x() + b.get_width() / 2, v * 1.12), ha="center", fontsize=8.5)
    eje.set_yscale("log")
    eje.set_ylim(10, 30000)
    eje.set_yticks([10, 60, 600, 3600, 18000], ["10 s", "1 min", "10 min", "1 h", "5 h"])
    eje.minorticks_off()
    eje.set_ylabel("Tiempo (escala logarítmica)")
    eje.set_xticks(range(len(filas)), etiquetas)
    eje.grid(axis="y", color="#e2e2e2", linewidth=0.6)
    eje.set_axisbelow(True)
    eje.legend(frameon=False, loc="upper center", bbox_to_anchor=(0.5, 1.13), ncol=2, fontsize=9)
    fig.tight_layout()
    fig.savefig(destino, facecolor="white")
    plt.close(fig)
    return list(zip(manual, agente))


# ------------------------------------------------------------------ capturas
def recorte(origen: str, caja: tuple[int, int, int, int], destino: Path, ancho_max: int = 1440) -> None:
    im = Image.open(CAPTURAS / origen).convert("RGB").crop(caja)
    if im.width > ancho_max:
        im = im.resize((ancho_max, round(im.height * ancho_max / im.width)), Image.LANCZOS)
    im.save(destino, optimize=True)


def composicion(origen: str, cajas: list[tuple[int, int, int, int]], destino: Path) -> None:
    """Une recortes verticales de la misma captura, separados por una línea discontinua (contenido omitido)."""
    im = Image.open(CAPTURAS / origen).convert("RGB")
    partes = [im.crop(c) for c in cajas]
    separacion = 28
    total = Image.new("RGB", (max(p.width for p in partes), sum(p.height for p in partes) + separacion * (len(partes) - 1)), "white")
    y = 0
    dibujo = ImageDraw.Draw(total)
    for k, p in enumerate(partes):
        total.paste(p, (0, y))
        y += p.height
        if k < len(partes) - 1:
            for x in range(0, total.width, 18):
                dibujo.line([(x, y + separacion // 2), (x + 9, y + separacion // 2)], fill=(120, 120, 120), width=2)
            y += separacion
    total.save(destino, optimize=True)


def lado_a_lado(origen: str, cajas: list[tuple[int, int, int, int]], destino: Path) -> None:
    """Pone recortes de la misma captura uno debajo del otro, alineados a la izquierda."""
    im = Image.open(CAPTURAS / origen).convert("RGB")
    partes = [im.crop(c) for c in cajas]
    margen = 16
    total = Image.new("RGB", (max(p.width for p in partes), sum(p.height for p in partes) + margen * (len(partes) - 1)), "white")
    y = 0
    for p in partes:
        total.paste(p, (0, y))
        y += p.height + margen
    total.save(destino, optimize=True)


def main() -> None:
    FIG.mkdir(exist_ok=True)
    mermaid(FUENTES / "fig-01-protocolo.mmd", FIG / "fig-01-protocolo.png", 1000, 1200)
    mermaid(FUENTES / "fig-02-niveles.mmd", FIG / "fig-02-niveles.png", 1200, 700)
    r1 = grafico_tiempo_costo(FIG / "fig-03-tiempo-costo.png")
    r2 = grafico_cobertura(FIG / "fig-04-cobertura-nivel.png")
    mermaid(FUENTES / "fig-05-topologia.mmd", FIG / "fig-05-topologia.png", 1100, 1100)
    lado_a_lado("01-especificacion.png", [(20, 70, 512, 270), (594, 150, 1381, 468)], FIG / "fig-06-especificacion.png")
    composicion("03-plan-de-despliegue-intento1.png", [(573, 1155, 1400, 1490), (573, 2440, 1400, 2590)],
                FIG / "fig-07-plan-aprobacion.png")
    recorte("05-desplegado-intento2.png", (573, 402, 1400, 690), FIG / "fig-08-desplegado.png")
    recorte("07-aplicacion-desplegada.png", (430, 200, 1010, 700), FIG / "fig-09-aplicacion.png")
    r3 = grafico_manual(FIG / "fig-10-manual-vs-agente.png")
    print(json.dumps({"tiempo_costo_medianas": {f"{k[0]}-T{k[1] - 1}": v for k, v in r1.items()},
                      "cobertura": r2, "manual_agente": r3}, ensure_ascii=False))


if __name__ == "__main__":
    main()
