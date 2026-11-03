import json

from agent.schemas import Especificacion
from grounding.catalog import Catalogo
from grounding.schema_index import IndiceEsquema

ESQUEMA = {
    "format_version": "1.0",
    "provider_schemas": {
        "registry.terraform.io/hashicorp/google": {
            "resource_schemas": {
                "google_storage_bucket": {
                    "block": {
                        "attributes": {
                            "name": {"type": "string", "required": True},
                            "location": {"type": "string", "required": True},
                            "force_destroy": {"type": "bool", "optional": True},
                            "labels": {"type": ["map", "string"], "optional": True},
                            "self_link": {"type": "string", "computed": True},
                            "viejo": {"type": "string", "optional": True, "deprecated": True},
                        },
                        "block_types": {
                            "versioning": {"nesting_mode": "list", "max_items": 1,
                                           "block": {"attributes": {"enabled": {"type": "bool", "required": True}}}},
                        },
                    }
                }
            }
        }
    },
}


def test_resumen_del_esquema(tmp_path):
    ruta = tmp_path / "esquema.json"
    ruta.write_text(json.dumps(ESQUEMA))
    idx = IndiceEsquema.cargar(ruta)
    texto = idx.resumen("google_storage_bucket")
    assert "requeridos: location (string), name (string)" in texto
    assert "labels (map(string))" in texto
    assert "self_link" not in texto      # solo calculado
    assert "viejo" not in texto          # obsoleto
    assert "bloque versioning [máx. 1]" in texto
    assert "sin esquema" in idx.resumen("google_inexistente")


def test_planificar_sql_privado_agrega_red():
    cat = Catalogo.cargar()
    spec = Especificacion(servicios=[
        {"tipo": "cloud_run", "nombre": "api", "publico": True},
        {"tipo": "cloud_sql_postgres", "nombre": "db", "parametros": {"ip_publica": False}},
    ])
    plan = cat.planificar(spec)
    assert "google_cloud_run_v2_service_iam_member" in plan.tipos_recurso
    assert "google_service_networking_connection" in plan.tipos_recurso
    assert plan.servicios_implicitos == ["vpc"]
    assert "google_compute_network" in plan.tipos_recurso
    assert set(plan.tipos_recurso) <= cat.tipos_permitidos


def test_planificar_bucket_privado_no_agrega_iam_publico():
    cat = Catalogo.cargar()
    plan = cat.planificar(Especificacion(servicios=[{"tipo": "cloud_storage", "nombre": "datos"}]))
    assert plan.tipos_recurso == ["google_storage_bucket"]
    assert "cloud_storage:" in cat.notas_para(plan)
