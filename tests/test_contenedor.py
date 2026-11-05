"""Pruebas que solo corren dentro del contenedor del sandbox (SANDBOX_CONTENEDOR=1).

    docker compose run --rm pruebas

Usan Terraform, TFLint y Checkov reales, sin LLM ni credenciales de GCP.
"""
import os
import socket
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from sandbox.local_runner import ValidadorLocal, _entorno_base

pytestmark = pytest.mark.skipif(os.environ.get("SANDBOX_CONTENEDOR") != "1",
                                reason="solo dentro del contenedor del sandbox")

PROYECTO_COMPLETO = Path(__file__).parent / "datos" / "proyecto_completo"


def _init(directorio: Path, versions_tf: str) -> subprocess.CompletedProcess:
    (directorio / "versions.tf").write_text(versions_tf, encoding="utf-8")
    return subprocess.run(["terraform", "init", "-input=false", "-no-color"], cwd=directorio,
                          env=_entorno_base(None, "southamerica-west1", None),
                          capture_output=True, text=True, timeout=300)


def test_validador_corre_todos_los_controles_sin_internet():
    """El proyecto de los 6 servicios pasa guard, fmt, init, validate, TFLint y Checkov reales."""
    archivos = {p.name: p.read_text(encoding="utf-8") for p in PROYECTO_COMPLETO.glob("*.tf")}
    v = ValidadorLocal(None, "southamerica-west1", None, checkov_bloqueantes={"CKV_GCP_2", "CKV_GCP_3"},
                       tflint_config=os.environ.get("TFLINT_CONFIG"))
    res = v.validar(archivos)
    estados = {c.control: (c.ok, c.omitido) for c in res.controles}
    fallos = {c.control: [d.texto() for d in c.errores] for c in res.controles if not c.ok}
    assert fallos == {}
    assert estados == {"guard": (True, False), "fmt": (True, False), "init": (True, False),
                       "validate": (True, False), "tflint": (True, False), "checkov": (True, False),
                       "plan": (True, True)}   # plan omitido: sin proyecto ni cuenta Viewer
    assert {"terraform", "google", "google-beta", "tflint", "tflint-ruleset-google", "checkov"} <= set(res.versiones)


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
