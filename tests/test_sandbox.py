import json
import os
from pathlib import Path

import pytest

from sandbox import guard
from sandbox.local_runner import (
    PERMITIDOS_EVALUACION,
    ComandoNoPermitido,
    _entorno_base,
    _Ejecutor,
    clasificar_origen,
    parse_checkov_json,
    parse_plan_json_lines,
    parse_show_json,
    parse_texto_errores,
    parse_tflint_json,
    parse_validate_json,
    parse_version_json,
)

S1, S2, S3, S4 = (pytest.mark.seguridad(f"S{i}") for i in range(1, 5))

BASE = 'terraform {\n  required_providers {\n    google = { source = "hashicorp/google" }\n  }\n}\nprovider "google" {}\n'
PROYECTO_COMPLETO = Path(__file__).parent / "datos" / "proyecto_completo"


def test_guard_acepta_codigo_normal():
    assert guard.revisar({"versions.tf": BASE, "main.tf": 'resource "google_storage_bucket" "b" {}\n'}) == []


def test_guard_acepta_proyecto_realista():
    """Los 6 servicios del catálogo con heredocs, interpolaciones, directivas, dynamic,
    check, moved y comentarios que nombran lo prohibido. No debe haber falsos positivos."""
    archivos = {p.name: p.read_text(encoding="utf-8") for p in PROYECTO_COMPLETO.glob("*.tf")}
    assert len(archivos) == 5
    assert guard.revisar(archivos) == []


@pytest.mark.parametrize("codigo,palabra", [
    pytest.param('provider "aws" {}\n', "aws", marks=S2),
    pytest.param('terraform {\n  required_providers {\n    aws = { source = "hashicorp/aws" }\n  }\n}\n',
                 "hashicorp/aws", marks=S2),
    pytest.param('resource "aws_s3_bucket" "b" {}\n', "aws_s3_bucket", marks=S2),
    pytest.param('data "external" "x" { program = ["sh"] }\n', "external", marks=S3),
    pytest.param('resource "google_storage_bucket" "b" {\n  provisioner "local-exec" { command = "id" }\n}\n',
                 "provisioners", marks=S3),
    pytest.param('data "http" "x" { url = "https://e.com" }\n', "http", marks=S4),
    pytest.param('terraform {\n  backend "gcs" {}\n}\n', "backend", marks=S4),
    pytest.param('module "m" {\n  source = "github.com/x/y"\n}\n', "Módulo", marks=S4),
])
def test_guard_rechaza(codigo, palabra):
    diags = guard.revisar({"main.tf": codigo})
    assert diags and any(palabra in d.mensaje for d in diags)


# Variantes que la versión anterior (expresiones regulares sobre el texto) dejaba pasar.
@pytest.mark.parametrize("codigo,palabra", [
    # S2: otro proveedor
    pytest.param('provider aws {\n  region = "us-east-1"\n}\n', "aws", marks=S2),
    pytest.param('terraform {\n  required_providers {\n    google = {\n      source = "hashicorp/google"\n    }\n'
                 '    aws = {\n      source = "hashicorp/aws"\n    }\n  }\n}\n', "hashicorp/aws", marks=S2),
    pytest.param('terraform {\n  required_providers {\n    google = { source: "hashicorp/aws" }\n  }\n}\n',
                 "hashicorp/aws", marks=S2),
    pytest.param('terraform {\n  required_providers {\n    google = { "source" = "hashicorp/aws" }\n  }\n}\n',
                 "hashicorp/aws", marks=S2),
    pytest.param('terraform {\n  required_providers {\n    google = { source = ("hashicorp/aws") }\n  }\n}\n',
                 "cadena literal", marks=S2),
    pytest.param('terraform {\n  required_providers {\n    google = { source = "evil.example.com/hashicorp/google" }\n'
                 '  }\n}\n', "evil.example.com", marks=S2),
    pytest.param('terraform {\n  required_providers {\n    random = "~> 3.0"\n  }\n}\n', "random", marks=S2),
    pytest.param('resource "google_storage_bucket" "b" {\n  provider = aws.west\n}\n', "aws.west", marks=S2),
    pytest.param('resource "googleworkspace_user" "u" {}\n', "googleworkspace_user", marks=S2),
    # S3: ejecución local y lectura de archivos o credenciales del sandbox
    pytest.param('data external x {\n  program = ["sh", "-c", "id"]\n}\n', "external", marks=S3),
    pytest.param('resource "terraform_data" "x" {\n  provisioner "local-exec" {\n    command = "id"\n  }\n}\n',
                 "provisioners", marks=S3),
    pytest.param('resource "google_storage_bucket" "b" {\n  dynamic "provisioner" {\n    for_each = [1]\n'
                 '    content {}\n  }\n}\n', "provisioners", marks=S3),
    pytest.param('resource "google_storage_bucket" "b" {\n  connection {\n    host = "x"\n  }\n}\n', "connection",
                 marks=S3),
    pytest.param('resource "google_storage_bucket" "b" {\n  name = "x-${file("/run/secrets/adc")}"\n}\n', "file()",
                 marks=S3),
    pytest.param('locals {\n  a = <<EOT\n${filebase64("~/.config/gcloud/application_default_credentials.json")}\n'
                 'EOT\n}\n', "filebase64()", marks=S3),
    pytest.param('locals {\n  a = templatefile ("x.tpl", {})\n}\n', "templatefile()", marks=S3),
    pytest.param('provider "google" {\n  credentials = "adc.json"\n}\n', "credentials", marks=S3),
    pytest.param('provider "google" {\n  impersonate_service_account = "deploy@p.iam.gserviceaccount.com"\n}\n',
                 "impersonate_service_account", marks=S3),
    pytest.param('data "google_client_config" "c" {}\n', "google_client_config", marks=S3),
    pytest.param('data "google_service_account_access_token" "t" {\n  target_service_account = "x"\n  scopes = []\n}\n',
                 "google_service_account_access_token", marks=S3),
    pytest.param('import {\n  to = google_storage_bucket.b\n  id = "bucket-ajeno"\n}\n', "import", marks=S3),
    pytest.param('ephemeral "google_service_account_access_token" "t" {}\n', "ephemeral", marks=S3),
    # S4: red
    pytest.param('data "http" "x" {\n  url = "https://example.com"\n}\n', "http", marks=S4),
    pytest.param('check "c" {\n  data "http" "x" {\n    url = "https://example.com"\n  }\n  assert {\n'
                 '    condition     = true\n    error_message = "x"\n  }\n}\n', "http", marks=S4),
    pytest.param('terraform {\n  backend "http" {\n    address = "https://example.com"\n  }\n}\n', "backend", marks=S4),
    pytest.param('terraform {\n  cloud {\n    organization = "x"\n  }\n}\n', "cloud", marks=S4),
    pytest.param('data "terraform_remote_state" "r" {\n  backend = "gcs"\n}\n', "terraform_remote_state", marks=S4),
    pytest.param('provider "google" {\n  storage_custom_endpoint = "https://storage.example.com/"\n}\n', "endpoints",
                 marks=S4),
    pytest.param('  module "m" {\n    source = "git::https://example.com/x.git"\n  }\n', "Módulo", marks=S4),
    pytest.param('module "m" {\n  source = "./"\n}\n', "Módulo", marks=S4),
])
def test_guard_rechaza_variantes(codigo, palabra):
    diags = guard.revisar({"main.tf": codigo})
    assert diags and any(palabra in d.mensaje for d in diags), [d.mensaje for d in diags]


def test_guard_ignora_comentarios_y_cadenas():
    codigo = (
        '# provisioner "local-exec" y data "external" están prohibidos; file("x") también\n'
        "/* provider \"aws\" {} */\n"
        'resource "google_storage_bucket" "b" {\n'
        '  name   = "a-b" // module "m" {}\n'
        '  labels = { nota = "usa file(x) y provisioner \\"y\\" { }" }\n'
        "}\n"
    )
    assert guard.revisar({"main.tf": codigo}) == []


@pytest.mark.parametrize("codigo", [
    'resource "google_storage_bucket" "b" {\n  name = "sin cerrar\n}\n',
    'resource "google_storage_bucket" "b" {\n  name = "x"\n',
    'locals {\n  a = <<EOT\nsin marcador final\n}\n',
    '/* comentario sin cerrar\nresource "google_storage_bucket" "b" {}\n',
    'resource "google_storage_bucket" "b" {\n  name = "x"\n}\n}\n',
])
def test_guard_falla_cerrada_si_no_puede_analizar(codigo):
    diags = guard.revisar({"main.tf": codigo})
    assert diags and "No se pudo analizar" in diags[0].mensaje and diags[0].linea


@pytest.mark.parametrize("nombre", ["main.tf.json", "terraform.tfvars", "x.auto.tfvars", ".terraformrc", "sub/main.tf",
                                    "Main.tf"])
def test_guard_rechaza_nombres_de_archivo(nombre):
    diags = guard.revisar({nombre: 'resource "google_storage_bucket" "b" {}\n'})
    assert diags and "Nombre de archivo no permitido" in diags[0].mensaje


def test_guard_informa_archivo_y_linea():
    diags = guard.revisar({"versions.tf": BASE, "main.tf": '\n\ndata "http" "x" {\n  url = "https://e.com"\n}\n'})
    assert {(d.archivo, d.linea) for d in diags} == {("main.tf", 3)}


@S1
def test_ejecutor_de_evaluacion_no_permite_apply():
    ej = _Ejecutor(PERMITIDOS_EVALUACION)
    for sub in ("apply", "destroy", "import", "state"):
        with pytest.raises(ComandoNoPermitido):
            ej.terraform_cmd([sub], Path("."), {})


@S3
def test_entorno_de_terraform_sin_claves_del_llm(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "secreto")
    monkeypatch.setenv("OPENAI_API_KEY", "secreto")
    monkeypatch.setenv("GOOGLE_IMPERSONATE_SERVICE_ACCOUNT", "otra@p.iam.gserviceaccount.com")
    env = _entorno_base("p", "southamerica-west1", "viewer@p.iam.gserviceaccount.com")
    assert "GEMINI_API_KEY" not in env and "OPENAI_API_KEY" not in env
    assert env["GOOGLE_IMPERSONATE_SERVICE_ACCOUNT"] == "viewer@p.iam.gserviceaccount.com"
    assert "GOOGLE_IMPERSONATE_SERVICE_ACCOUNT" not in _entorno_base("p", "r", None)
    assert os.environ["GEMINI_API_KEY"] == "secreto"   # el proceso del agente la conserva


def test_parse_version():
    salida = json.dumps({"terraform_version": "1.16.7", "provider_selections": {
        "registry.terraform.io/hashicorp/google": "8.8.1", "registry.terraform.io/hashicorp/google-beta": "8.8.1"}})
    assert parse_version_json(salida) == {"terraform": "1.16.7", "google": "8.8.1", "google-beta": "8.8.1"}
    assert parse_version_json("no es json") == {}


def test_parse_validate():
    salida = json.dumps({"valid": False, "diagnostics": [{
        "severity": "error", "summary": "Unsupported argument",
        "detail": 'An argument named "foo" is not expected here.',
        "range": {"filename": "main.tf", "start": {"line": 3, "column": 3}}}]})
    d = parse_validate_json(salida)[0]
    assert (d.archivo, d.linea, d.severidad) == ("main.tf", 3, "error")
    assert "foo" in d.mensaje


def test_parse_tflint():
    salida = json.dumps({"issues": [{"rule": {"name": "google_compute_instance_invalid_machine_type",
                                              "severity": "error"},
                                     "message": '"n9" is an invalid as machine type',
                                     "range": {"filename": "main.tf", "start": {"line": 7}}}], "errors": []})
    d = parse_tflint_json(salida)[0]
    assert d.regla == "google_compute_instance_invalid_machine_type" and d.linea == 7


def test_parse_checkov_bloqueantes():
    salida = json.dumps({"results": {"failed_checks": [
        {"check_id": "CKV_GCP_2", "check_name": "SSH abierto", "file_path": "/main.tf",
         "file_line_range": [10, 20], "resource": "google_compute_firewall.ssh"},
        {"check_id": "CKV_GCP_62", "check_name": "Logs de acceso", "file_path": "/main.tf",
         "file_line_range": [1, 5], "resource": "google_storage_bucket.b"}]}})
    diags, ok = parse_checkov_json(salida, {"CKV_GCP_2"})
    assert not ok and len(diags) == 2
    assert diags[0].tipo_recurso == "google_compute_firewall" and diags[0].archivo == "main.tf"
    _, ok2 = parse_checkov_json(salida, set())
    assert ok2


def test_parse_plan_y_show():
    lineas = "\n".join([
        json.dumps({"type": "version", "terraform": "1.9.0"}),
        json.dumps({"type": "diagnostic", "diagnostic": {"severity": "error", "summary": "Error 403",
                                                         "detail": "Permission denied", "address": "google_x.y"}}),
    ])
    d = parse_plan_json_lines(lineas)[0]
    assert d.tipo_recurso == "google_x" and d.control == "plan"
    show = json.dumps({"resource_changes": [
        {"address": "google_storage_bucket.b", "mode": "managed", "type": "google_storage_bucket",
         "change": {"actions": ["create"]}},
        {"address": "data.google_project.p", "mode": "data", "type": "google_project", "change": {"actions": ["read"]}},
    ]})
    cambios = parse_show_json(show)
    assert [c.tipo for c in cambios] == ["google_storage_bucket"]


def test_texto_errores_y_origen():
    err = 'Error: Argument or block definition required\n\n  on main.tf line 4:\n   4: x\n'
    d = parse_texto_errores(err, "fmt")[0]
    assert (d.archivo, d.linea) == ("main.tf", 4)
    assert clasificar_origen("Error 429: Quota exceeded") == "entorno"
    assert clasificar_origen("Unsupported argument") == "codigo"
