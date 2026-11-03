import json

from agent.deploy import buscar_url, ejecutar_despliegue, verificar_plan
from grounding.catalog import Catalogo
from sandbox.contract import CambioRecurso
from sandbox.fake import DesplegadorSimulado

PERMITIDOS = Catalogo.cargar().tipos_permitidos
CAMBIOS = [CambioRecurso(direccion="google_cloud_run_v2_service.app", tipo="google_cloud_run_v2_service",
                         acciones=["create"])]
ARCHIVOS = {"main.tf": 'resource "google_cloud_run_v2_service" "app" {}\n'}


def respuestas(*valores):
    pila = list(valores)
    return lambda _msg: pila.pop(0)


def test_verificar_plan_rechaza_fuera_de_catalogo_y_destruccion():   # S8
    malos = [CambioRecurso(direccion="google_compute_instance.vm", tipo="google_compute_instance", acciones=["create"]),
             CambioRecurso(direccion="google_storage_bucket.b", tipo="google_storage_bucket",
                           acciones=["delete", "create"])]
    problemas = verificar_plan(malos, PERMITIDOS)
    assert len(problemas) == 2
    assert verificar_plan(CAMBIOS, PERMITIDOS) == []


def test_sin_aprobacion_no_hay_apply(tmp_path):                     # S7
    d = DesplegadorSimulado(CAMBIOS, {"service_url": "https://app.run.app"})
    res = ejecutar_despliegue(ARCHIVOS, d, proyecto_id="mi-proyecto", tipos_permitidos=PERMITIDOS,
                              confirmar=respuestas("si"), dir_evidencia=tmp_path)
    assert res.estado == "cancelado" and not d.aplicado
    assert json.loads((tmp_path / "resultado_despliegue.json").read_text())["estado"] == "cancelado"


def test_plan_fuera_de_catalogo_no_pregunta(tmp_path):
    malos = [CambioRecurso(direccion="google_compute_instance.vm", tipo="google_compute_instance", acciones=["create"])]
    d = DesplegadorSimulado(malos)
    res = ejecutar_despliegue(ARCHIVOS, d, proyecto_id="p", tipos_permitidos=PERMITIDOS,
                              confirmar=lambda m: (_ for _ in ()).throw(AssertionError("no debe preguntar")),
                              dir_evidencia=tmp_path)
    assert res.estado == "rechazado" and not d.aplicado


def test_flujo_completo_con_destruccion(tmp_path):
    d = DesplegadorSimulado(CAMBIOS, {"service_url": "https://app.run.app"})
    res = ejecutar_despliegue(ARCHIVOS, d, proyecto_id="mi-proyecto", tipos_permitidos=PERMITIDOS,
                              confirmar=respuestas("mi-proyecto", "mi-proyecto"), dir_evidencia=tmp_path,
                              probar=lambda url: {"url": url, "codigo": 200, "ok": True})
    assert res.aprobado and res.aplicado and res.destruido
    assert res.estado == "destruido" and res.prueba_http["codigo"] == 200
    for archivo in ("plan.txt", "apply.log", "http.txt", "destroy.log", "resultado_despliegue.json"):
        assert (tmp_path / archivo).exists()


def test_conservar_recursos_deja_comando(tmp_path):
    d = DesplegadorSimulado(CAMBIOS, {"service_url": "https://app.run.app"})
    res = ejecutar_despliegue(ARCHIVOS, d, proyecto_id="p", tipos_permitidos=PERMITIDOS,
                              confirmar=respuestas("p", ""), dir_evidencia=tmp_path,
                              probar=lambda url: {"codigo": 200, "ok": True})
    assert res.estado == "desplegado" and not d.destruido
    assert "python -m agent destruir" in res.motivo


def test_buscar_url():
    assert buscar_url({"service_url": "https://a.run.app"}) == "https://a.run.app"
    assert buscar_url({"otro": "https://b.run.app", "n": 1}) == "https://b.run.app"
    assert buscar_url({"x": "http://inseguro"}) is None
