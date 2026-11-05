"""Pruebas que solo corren dentro del contenedor del sandbox (SANDBOX_CONTENEDOR=1).

    docker compose run --rm pruebas

Usan Terraform, TFLint y Checkov reales, sin LLM ni credenciales de GCP.
"""
import json
import os
import socket
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from agent.config import Config
from agent.llm import ClienteLLM
from agent.parsing import formatear_archivos
from agent.pipeline import Agente
from agent.schemas import Escenario
from grounding.catalog import Catalogo
from grounding.schema_index import IndiceEsquema
from sandbox.local_runner import ValidadorLocal, _entorno_base

pytestmark = pytest.mark.skipif(os.environ.get("SANDBOX_CONTENEDOR") != "1",
                                reason="solo dentro del contenedor del sandbox")

DATOS = Path(__file__).parent / "datos"
PROYECTO_COMPLETO = DATOS / "proyecto_completo"
CASO_REAL = DATOS / "caso_real"
ESCENARIO_REAL = Path(__file__).parents[1] / "casos" / "caso_real.yaml"


def _leer(directorio: Path) -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(directorio.glob("*.tf"))}


def _validador() -> ValidadorLocal:
    return ValidadorLocal(None, "southamerica-west1", None, checkov_bloqueantes={"CKV_GCP_2", "CKV_GCP_3"},
                          tflint_config=os.environ.get("TFLINT_CONFIG"))


class LLMGuion:
    """Simula litellm.completion con respuestas fijas, en orden."""

    def __init__(self, respuestas: list[str]) -> None:
        self.respuestas = list(respuestas)
        self.prompts: list[str] = []

    def __call__(self, **kwargs):
        self.prompts.append(kwargs["messages"][1]["content"])
        return {"choices": [{"message": {"content": self.respuestas.pop(0)}}],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 500}}


ESPEC_REAL = {"proyecto": "picanteria", "region": "southamerica-west1", "servicios": [
    {"tipo": "cloud_run", "nombre": "api", "publico": True, "parametros": {"puerto": 3000}},
    {"tipo": "cloud_run", "nombre": "web", "publico": True, "parametros": {"puerto": 80}},
    {"tipo": "cloud_sql_postgres", "nombre": "picanteria", "parametros": {"version": "POSTGRES_17", "ip_publica": True}},
    {"tipo": "secret_manager", "nombre": "secretos"},
    {"tipo": "iam", "nombre": "run"},
]}


def test_agente_t1_repara_con_errores_reales_del_sandbox():
    """Bucle T1 completo con el validador real y un LLM simulado, sobre el caso real (REAL-01).

    1.ª respuesta: credentials = file(...) en el provider y `port` en vez de `ports {}` -> lo rechaza el guard.
    2.ª respuesta: sin credentials, pero sigue `port`                                  -> lo rechaza validate.
    3.ª respuesta: el código correcto                                                  -> pasan todos los controles.
    """
    bueno = _leer(CASO_REAL)
    con_port = dict(bueno, **{"main.tf": bueno["main.tf"].replace(
        "      ports {\n        container_port = 3000\n      }\n", "      port = 3000\n", 1)})
    con_credentials = dict(con_port, **{"providers.tf": bueno["providers.tf"].replace(
        "  region  = var.region\n", '  region  = var.region\n  credentials = file("cuenta.json")\n')})
    assert con_port["main.tf"] != bueno["main.tf"] and con_credentials["providers.tf"] != bueno["providers.tf"]

    guion = LLMGuion([json.dumps(ESPEC_REAL), formatear_archivos(con_credentials), formatear_archivos(con_port),
                      formatear_archivos(bueno)])
    llm = ClienteLLM("gemini/gemini-3.8-flash", 0.75, 3.75, completion_fn=guion, espera_s=0)
    esquema = IndiceEsquema.cargar("grounding/esquema_catalogo.json")
    agente = Agente(llm, _validador(), Catalogo.cargar(), Config(), esquema)
    res = agente.correr(Escenario.desde_yaml(ESCENARIO_REAL), "T1")

    assert [i.control_fallido for i in res.intentos_detalle] == ["guard", "validate", None]
    assert res.intentos == 2 and res.validate_ok and res.tipo_error == "ninguno"
    assert "credentials" in guion.prompts[2] and "file()" in guion.prompts[2]
    assert 'An argument named "port" is not expected here' in guion.prompts[3]
    assert res.cobertura_codigo == 1.0
    estados = {c.control: c.omitido for c in res.validacion.controles}
    assert estados == {"guard": False, "fmt": False, "init": False, "validate": False, "tflint": False,
                       "checkov": False, "plan": True}
    fila = res.fila_csv()
    assert fila["version_terraform"] and fila["version_proveedor"]


def _init(directorio: Path, versions_tf: str) -> subprocess.CompletedProcess:
    (directorio / "versions.tf").write_text(versions_tf, encoding="utf-8")
    return subprocess.run(["terraform", "init", "-input=false", "-no-color"], cwd=directorio,
                          env=_entorno_base(None, "southamerica-west1", None),
                          capture_output=True, text=True, timeout=300)


@pytest.mark.parametrize("directorio", [PROYECTO_COMPLETO, CASO_REAL], ids=["seis_servicios", "caso_real"])
def test_validador_corre_todos_los_controles_sin_internet(directorio):
    """El código pasa guard, fmt, init, validate, TFLint y Checkov reales, sin salir a internet."""
    res = _validador().validar(_leer(directorio))
    estados = {c.control: (c.ok, c.omitido) for c in res.controles}
    fallos = {c.control: [d.texto() for d in c.errores] for c in res.controles if not c.ok}
    assert fallos == {}
    assert estados == {"guard": (True, False), "fmt": (True, False), "init": (True, False),
                       "validate": (True, False), "tflint": (True, False), "checkov": (True, False),
                       "plan": (True, True)}   # plan omitido: sin proyecto ni cuenta Viewer
    assert {"terraform", "google", "tflint", "tflint-ruleset-google", "checkov"} <= set(res.versiones)


@pytest.mark.seguridad("S2")
@pytest.mark.parametrize("requerido,palabra", [
    ('random = { source = "hashicorp/random" }', "hashicorp/random"),
    ('google = { source = "hashicorp/google", version = "6.0.0" }', "hashicorp/google"),
])
def test_init_solo_instala_proveedores_del_espejo(tmp_path, requerido, palabra):
    """Segunda capa de S2: aunque el guard fallara, Terraform no puede descargar otro proveedor ni otra versión."""
    p = _init(tmp_path, f"terraform {{\n  required_providers {{\n    {requerido}\n  }}\n}}\n")
    assert p.returncode != 0
    assert palabra in p.stdout + p.stderr


@pytest.mark.seguridad("S6")
@pytest.mark.parametrize("destino", [("1.1.1.1", 443), ("8.8.8.8", 53), ("142.250.0.1", 443)])
def test_sin_ruta_directa_a_internet(destino):
    with pytest.raises(OSError):
        socket.create_connection(destino, timeout=5).close()


@pytest.mark.seguridad("S6")
@pytest.mark.skipif(not os.environ.get("HTTPS_PROXY"), reason="sin proxy de salida configurado")
@pytest.mark.parametrize("url", ["https://example.com", "https://registry.terraform.io", "https://8.8.8.8"])
def test_proxy_rechaza_destinos_fuera_de_la_lista(url):
    with pytest.raises(urllib.error.URLError) as e:
        urllib.request.urlopen(url, timeout=15)
    assert "403" in str(e.value)
