import json
from pathlib import Path

import pytest

from sandbox import guard
from sandbox.local_runner import (
    PERMITIDOS_EVALUACION,
    ComandoNoPermitido,
    _Ejecutor,
    clasificar_origen,
    parse_checkov_json,
    parse_plan_json_lines,
    parse_show_json,
    parse_texto_errores,
    parse_tflint_json,
    parse_validate_json,
)

BASE = 'terraform {\n  required_providers {\n    google = { source = "hashicorp/google" }\n  }\n}\nprovider "google" {}\n'


def test_guard_acepta_codigo_normal():
    assert guard.revisar({"versions.tf": BASE, "main.tf": 'resource "google_storage_bucket" "b" {}\n'}) == []


@pytest.mark.parametrize("codigo,palabra", [
    ('provider "aws" {}\n', "aws"),                                          # S2
    ('terraform {\n  required_providers {\n    aws = { source = "hashicorp/aws" }\n  }\n}\n', "hashicorp/aws"),
    ('data "external" "x" { program = ["sh"] }\n', "external"),               # S3
    ('resource "google_storage_bucket" "b" {\n  provisioner "local-exec" { command = "id" }\n}\n', "provisioners"),
    ('data "http" "x" { url = "https://e.com" }\n', "http"),                  # S4
    ('terraform {\n  backend "gcs" {}\n}\n', "backend"),
    ('module "m" {\n  source = "github.com/x/y"\n}\n', "Módulo"),
    ('resource "aws_s3_bucket" "b" {}\n', "aws_s3_bucket"),
])
def test_guard_rechaza(codigo, palabra):
    diags = guard.revisar({"main.tf": codigo})
    assert diags and any(palabra in d.mensaje for d in diags)


def test_ejecutor_de_evaluacion_no_permite_apply():                         # S1
    ej = _Ejecutor(PERMITIDOS_EVALUACION)
    for sub in ("apply", "destroy", "import", "state"):
        with pytest.raises(ComandoNoPermitido):
            ej.terraform_cmd([sub], Path("."), {})


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
