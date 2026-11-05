"""Interfaz web: flujo completo y defensas del servidor, sin LLM real ni Terraform."""
import io
import json
import threading
import time
import urllib.error
import urllib.request
import zipfile
from importlib import resources
from pathlib import Path

import pytest

from agent.cli import _guardar
from agent.config import Config
from agent.llm import ClienteLLM
from agent.pipeline import Agente
from agent.web.servidor import REGIONES, Aplicacion, crear_servidor
from grounding.catalog import Catalogo
from sandbox.fake import ValidadorSimulado, resultado_error, resultado_ok

REQ = "Necesitamos un sitio web estático público y barato en Santiago de Chile."
SPEC_A = {"proyecto": "sitio", "region": "southamerica-west1",
          "servicios": [{"tipo": "cloud_storage", "nombre": "sitio", "publico": True}]}
SPEC_B = {"proyecto": "sitio", "region": "southamerica-west1",
          "servicios": [{"tipo": "cloud_storage", "nombre": "sitio", "publico": True,
                         "parametros": {"storage_class": "STANDARD"}}]}
CODIGO = '<file name="main.tf">\nresource "google_storage_bucket" "sitio" {\n  name = "x"\n}\n</file>'
CODIGO_OK = ('<file name="main.tf">\nresource "google_storage_bucket" "sitio" {\n  name = "x"\n}\n'
             'resource "google_storage_bucket_iam_member" "p" {\n  bucket = "x"\n}\n</file>')


class Guion:
    """Simula litellm.completion; si se le da un evento, la primera llamada espera a que se active."""

    def __init__(self, respuestas, bloqueo: threading.Event | None = None):
        self.respuestas = list(respuestas)
        self.prompts = []
        self.bloqueo = bloqueo

    def __call__(self, **kwargs):
        if self.bloqueo is not None:
            self.bloqueo.wait(5)
            self.bloqueo = None
        self.prompts.append(kwargs["messages"][1]["content"])
        return {"choices": [{"message": {"content": self.respuestas.pop(0)}}],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 500}}


@pytest.fixture
def web(tmp_path):
    servidores = []

    def levantar(respuestas, validador=None, bloqueo=None):
        fn = Guion(respuestas, bloqueo)
        llm = ClienteLLM("gemini/gemini-3.8-flash", 0.75, 3.75, completion_fn=fn, espera_s=0)
        agente = Agente(llm, validador or ValidadorSimulado(), Catalogo.cargar(), Config())
        info = {"modelo": "gemini/gemini-3.8-flash", "region": REGIONES[0], "regiones": list(REGIONES),
                "plan": False, "max_reparaciones": 5}
        srv = crear_servidor(Aplicacion(agente, tmp_path / "web", info, _guardar), "127.0.0.1", 0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servidores.append(srv)
        return f"http://127.0.0.1:{srv.server_address[1]}", fn

    yield levantar
    for s in servidores:
        s.shutdown()
        s.server_close()


def pedir(url, cuerpo=None, cabeceras=None):
    datos = None if cuerpo is None else json.dumps(cuerpo).encode()
    req = urllib.request.Request(url, data=datos, headers={"Content-Type": "application/json", **(cabeceras or {})})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def vista(base, sid):
    estado, _, cuerpo = pedir(f"{base}/api/sesiones/{sid}")
    assert estado == 200
    return json.loads(cuerpo)


def esperar(base, sid, *estados):
    limite = time.monotonic() + 5
    while time.monotonic() < limite:
        v = vista(base, sid)
        if v["estado"] in estados:
            return v
        time.sleep(0.02)
    raise AssertionError(f"La sesión no llegó a {estados}: {v['estado']}")


def test_flujo_completo_con_correccion_aprobacion_y_descarga(web, tmp_path):
    validador = ValidadorSimulado([
        lambda a: resultado_error(a, "validate", 'Unsupported argument "foo"', linea=2),
        lambda a: resultado_ok(a, ["google_storage_bucket", "google_storage_bucket_iam_member"]),
    ])
    base, fn = web([json.dumps(SPEC_A), json.dumps(SPEC_B), CODIGO, CODIGO_OK], validador)

    estado, _, cuerpo = pedir(f"{base}/api/sesiones", {"requerimiento": REQ, "region": "southamerica-west1"})
    assert estado == 202
    sid = json.loads(cuerpo)["id"]
    v = esperar(base, sid, "revision")
    assert v["revision"] == "A" and v["especificacion"]["servicios"][0]["tipo"] == "cloud_storage"

    estado, _, _ = pedir(f"{base}/api/sesiones/{sid}/correcciones", {"texto": "Usa la clase STANDARD."})
    assert estado == 202
    v = esperar(base, sid, "revision")
    assert v["revision"] == "B"
    # La corrección va dentro del requerimiento, junto con la especificación anterior; la plantilla no cambia.
    assert "Correcciones de la persona usuaria" in fn.prompts[1] and "Usa la clase STANDARD." in fn.prompts[1]
    assert "Especificación propuesta antes" in fn.prompts[1]

    estado, _, _ = pedir(f"{base}/api/sesiones/{sid}/aprobar", {})
    assert estado == 202
    v = esperar(base, sid, "terminado")
    r = v["resultado"]
    assert r["ok"] and r["plan"] == "ok" and r["reparaciones"] == 1
    assert [i["accion"] for i in v["intentos"]] == ["generar", "reparar"]
    assert [e["tipo"] for e in v["eventos"]] == [
        "requerimiento", "especificacion", "correccion", "especificacion", "aprobacion", "resultado"]
    assert [c["etapa"] for c in v["llamadas"]] == ["analizar", "analizar", "generar", "reparar"]
    assert v["costo_usd"] > 0

    # Se guarda fuera del árbol del experimento: resultados/web/<escenario>/T1-<run_id>
    carpeta = Path(v["carpeta"])
    assert carpeta.parent.parent == tmp_path / "web" and (carpeta / "resultado.json").exists()

    estado, cab, datos = pedir(f"{base}/api/sesiones/{sid}/proyecto.zip")
    assert estado == 200 and cab["Content-Type"] == "application/zip"
    nombres = zipfile.ZipFile(io.BytesIO(datos)).namelist()
    assert nombres == [f"{v['escenario_id'].lower()}/main.tf"]


def test_no_se_puede_aprobar_ni_descargar_antes_de_tiempo(web):
    bloqueo = threading.Event()
    base, _ = web([json.dumps(SPEC_A)], bloqueo=bloqueo)
    _, _, cuerpo = pedir(f"{base}/api/sesiones", {"requerimiento": REQ, "region": REGIONES[0]})
    sid = json.loads(cuerpo)["id"]
    assert vista(base, sid)["estado"] == "analizando"
    assert pedir(f"{base}/api/sesiones/{sid}/aprobar", {})[0] == 409
    assert pedir(f"{base}/api/sesiones/{sid}/proyecto.zip")[0] == 409
    # Un solo trabajo con el LLM a la vez: una segunda solicitud no gasta.
    assert pedir(f"{base}/api/sesiones", {"requerimiento": REQ, "region": REGIONES[0]})[0] == 409
    bloqueo.set()
    esperar(base, sid, "revision")


def test_entradas_invalidas(web):
    base, _ = web([])
    assert pedir(f"{base}/api/sesiones", {"requerimiento": "corto", "region": REGIONES[0]})[0] == 400
    assert pedir(f"{base}/api/sesiones", {"requerimiento": REQ, "region": "marte-1"})[0] == 400
    assert pedir(f"{base}/api/sesiones", {"requerimiento": "x" * 5000, "region": REGIONES[0]})[0] == 413
    assert pedir(f"{base}/api/sesiones", None, {"Content-Type": "text/plain"})[0] in (404, 415)
    req = urllib.request.Request(f"{base}/api/sesiones", data=b"requerimiento=x", headers={"Content-Type": "text/plain"})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=5)
    assert e.value.code == 415
    assert pedir(f"{base}/api/sesiones/0123456789abcdef")[0] == 404
    assert pedir(f"{base}/../pyproject.toml")[0] == 404
    assert pedir(f"{base}/api/sesiones/../../etc/passwd")[0] == 404


def test_solo_localhost_y_mismo_origen(web):
    base, _ = web([])
    assert pedir(f"{base}/api/estado", cabeceras={"Host": "atacante.example"})[0] == 403
    assert pedir(f"{base}/api/sesiones", {"requerimiento": REQ, "region": REGIONES[0]},
                 {"Origin": "http://atacante.example"})[0] == 403
    estado, _, cuerpo = pedir(f"{base}/api/estado")
    assert estado == 200 and json.loads(cuerpo)["plan"] is False


def test_pagina_y_cabeceras_de_seguridad(web):
    base, _ = web([])
    estado, cab, cuerpo = pedir(f"{base}/")
    assert estado == 200 and b"/app.js" in cuerpo
    assert "script-src 'self'" in cab["Content-Security-Policy"] and cab["X-Content-Type-Options"] == "nosniff"
    for ruta, tipo in (("/app.js", "text/javascript"), ("/app.css", "text/css")):
        estado, cab, _ = pedir(f"{base}{ruta}")
        assert estado == 200 and cab["Content-Type"].startswith(tipo)


def test_la_pagina_no_inserta_html_del_modelo():
    js = resources.files("agent.web").joinpath("estatico", "app.js").read_text(encoding="utf-8")
    assert "innerHTML" not in js.replace("nunca con innerHTML", "") and "insertAdjacentHTML" not in js
