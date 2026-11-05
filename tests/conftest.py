import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Pruebas de seguridad del plan (sección 5.8 del informe). Cada prueba automática lleva
# @pytest.mark.seguridad("Sx") y pytest imprime un resumen por prueba al final.
PRUEBAS_SEGURIDAD = {
    "S1": "El modo evaluación no ejecuta apply ni destroy",
    "S2": "Se rechaza código de otro proveedor",
    "S3": "Se rechaza la ejecución local y la lectura de archivos o credenciales",
    "S4": "Se rechazan las peticiones de red desde Terraform",
    "S5": "La cuenta Viewer no puede crear recursos",
    "S6": "El contenedor no sale a internet",
    "S7": "Sin aprobación humana no hay apply",
    "S8": "Se rechaza un plan con recursos fuera del catálogo o con destrucción",
}
_EVIDENCIA_APARTE = {
    "S5": "evidencia aparte: python -m sandbox.pruebas_seguridad s5",
    "S6": "evidencia completa: python -m sandbox.pruebas_seguridad s6",
}
_ids: dict[str, list[str]] = {}
_resultado: dict[str, str] = {}


def pytest_configure(config):
    config.addinivalue_line("markers", "seguridad(id): prueba de seguridad del plan (S1 a S8)")


def pytest_collection_modifyitems(items):
    for item in items:
        ids = [m.args[0] for m in item.iter_markers("seguridad")]
        if ids:
            _ids[item.nodeid] = ids


def pytest_runtest_logreport(report):
    if report.nodeid not in _ids or _resultado.get(report.nodeid) == "failed":
        return
    if report.when == "call" or report.outcome != "passed":
        _resultado[report.nodeid] = report.outcome


def pytest_terminal_summary(terminalreporter):
    if not _resultado:
        return
    tr = terminalreporter
    nombres = {"passed": ("pasó", "pasaron"), "failed": ("falló", "fallaron"), "skipped": ("omitida", "omitidas")}
    tr.write_sep("=", "Pruebas de seguridad")
    for sid, descripcion in PRUEBAS_SEGURIDAD.items():
        conteo = Counter(_resultado[n] for n, ids in _ids.items() if sid in ids and n in _resultado)
        if conteo["failed"]:
            estado = "FALLA"
        elif conteo["passed"]:
            estado = "OK"
        elif conteo["skipped"]:
            estado = "OMITIDA"
        else:
            estado = "-"
        detalle = ", ".join(f"{v} {nombres[k][v != 1]}" for k, v in conteo.items() if k in nombres)
        extra = "; ".join(x for x in (detalle, _EVIDENCIA_APARTE.get(sid, "")) if x)
        tr.write_line(f"{sid}  {estado:<8} {descripcion}" + (f"  ({extra})" if extra else ""))
