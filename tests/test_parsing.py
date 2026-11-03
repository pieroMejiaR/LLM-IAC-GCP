import pytest

from agent.parsing import (
    ArchivoNoPermitido,
    cobertura,
    extraer_archivos,
    extraer_json,
    recurso_en_linea,
    tipos_en_codigo,
)

RESPUESTA = '''Aquí va:
<file name="main.tf">
```hcl
resource "google_storage_bucket" "sitio" {
  name     = "x"
  location = "SOUTHAMERICA-WEST1"
}

resource "google_storage_bucket_iam_member" "publico" {
  bucket = google_storage_bucket.sitio.name
  role   = "roles/storage.objectViewer"
  member = "allUsers"
}
```
</file>
<file name="outputs.tf">
output "nombre" { value = google_storage_bucket.sitio.name }
</file>'''


def test_extrae_archivos_y_quita_cercas():
    archivos = extraer_archivos(RESPUESTA)
    assert set(archivos) == {"main.tf", "outputs.tf"}
    assert archivos["main.tf"].startswith('resource "google_storage_bucket"')
    assert "```" not in archivos["main.tf"]


@pytest.mark.parametrize("nombre", ["../main.tf", "sub/main.tf", "script.sh", ".terraformrc"])
def test_rechaza_nombres_peligrosos(nombre):
    with pytest.raises(ArchivoNoPermitido):
        extraer_archivos(f'<file name="{nombre}">x</file>')


def test_tipos_y_recurso_en_linea():
    archivos = extraer_archivos(RESPUESTA)
    assert tipos_en_codigo(archivos) == ["google_storage_bucket", "google_storage_bucket_iam_member"]
    assert recurso_en_linea(archivos, "main.tf", 2) == "google_storage_bucket"
    assert recurso_en_linea(archivos, "main.tf", 9) == "google_storage_bucket_iam_member"
    assert recurso_en_linea(archivos, "otro.tf", 1) is None


def test_extraer_json_tolera_texto_y_cercas():
    assert extraer_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extraer_json('Respuesta: {"a": {"b": 2}} fin') == {"a": {"b": 2}}
    with pytest.raises(ValueError):
        extraer_json("sin json")


def test_cobertura():
    assert cobertura(["a", "b"], ["a", "c"]) == 0.5
    assert cobertura([], ["a"]) is None
