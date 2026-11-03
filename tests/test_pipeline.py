import json

from agent.config import Config
from agent.llm import ClienteLLM
from agent.pipeline import Agente
from agent.schemas import Escenario
from grounding.catalog import Catalogo
from sandbox.fake import ValidadorSimulado, resultado_error, resultado_ok

ESCENARIO = Escenario(
    id="BAS-01", nivel="basico", titulo="Sitio", requerimiento="Un bucket público para un sitio estático.",
    servicios_esperados=["google_storage_bucket", "google_storage_bucket_iam_member"],
)
SPEC = {"proyecto": "sitio", "region": "southamerica-west1",
        "servicios": [{"tipo": "cloud_storage", "nombre": "sitio", "publico": True}]}
CODIGO = '<file name="main.tf">\nresource "google_storage_bucket" "sitio" {\n  name = "x"\n  location = "US"\n}\n</file>'
CODIGO_OK = ('<file name="main.tf">\nresource "google_storage_bucket" "sitio" {\n  name = "x"\n  location = "US"\n}\n'
             'resource "google_storage_bucket_iam_member" "p" {\n  bucket = "x"\n}\n</file>')


class LLMGuion:
    """Simula litellm.completion devolviendo respuestas en orden."""

    def __init__(self, respuestas):
        self.respuestas = list(respuestas)
        self.prompts = []

    def __call__(self, **kwargs):
        self.prompts.append(kwargs["messages"][1]["content"])
        texto = self.respuestas.pop(0)
        return {"choices": [{"message": {"content": texto}}],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 500}}


def agente(respuestas, validador, max_rep=5):
    fn = LLMGuion(respuestas)
    llm = ClienteLLM("gemini/gemini-3.8-flash", 0.75, 3.75, completion_fn=fn, espera_s=0)
    cfg = Config(max_reparaciones=max_rep)
    return Agente(llm, validador, Catalogo.cargar(), cfg), fn


def test_t1_repara_y_termina_ok():
    validador = ValidadorSimulado([
        lambda a: resultado_error(a, "validate", 'Unsupported argument "foo"', linea=3),
        lambda a: resultado_ok(a, ["google_storage_bucket", "google_storage_bucket_iam_member"]),
    ])
    ag, fn = agente([json.dumps(SPEC), CODIGO, CODIGO_OK], validador)
    res = ag.correr(ESCENARIO, "T1")
    assert res.validate_ok and res.plan_ok
    assert res.intentos == 1
    assert res.tipo_error == "ninguno"
    assert [i.accion for i in res.intentos_detalle] == ["generar", "reparar"]
    assert res.cobertura_plan == 1.0
    # El prompt de reparación trae el error y el esquema del recurso afectado
    assert 'Unsupported argument "foo"' in fn.prompts[2]
    assert "google_storage_bucket" in fn.prompts[2]
    # 3 llamadas de 1000 + 500 tokens: 3 * (1000*0.75 + 500*3.75) / 1e6
    assert abs(res.costo_usd - 3 * (750 + 1875) / 1e6) < 1e-12


def test_t1_agota_reparaciones():
    validador = ValidadorSimulado(regla=lambda a: resultado_error(a, "validate", "Invalid block"))
    ag, _ = agente([json.dumps(SPEC)] + [CODIGO] * 3, validador, max_rep=2)
    res = ag.correr(ESCENARIO, "T1")
    assert not res.validate_ok
    assert res.intentos == 2
    assert res.tipo_error == "codigo"
    assert "Invalid block" in res.error_final


def test_t1_no_repara_errores_del_entorno():
    validador = ValidadorSimulado(regla=lambda a: resultado_error(a, "plan", "quota exceeded", origen="entorno"))
    ag, fn = agente([json.dumps(SPEC), CODIGO], validador)
    res = ag.correr(ESCENARIO, "T1")
    assert res.intentos == 0
    assert res.tipo_error == "entorno"
    assert len(fn.prompts) == 2


def test_t1_reintenta_especificacion_invalida():
    malo = json.dumps({"servicios": [{"tipo": "gke", "nombre": "x"}]})
    ag, fn = agente([malo, json.dumps(SPEC), CODIGO_OK], ValidadorSimulado())
    res = ag.correr(ESCENARIO, "T1")
    assert res.especificacion is not None
    assert "no cumplió el esquema" in fn.prompts[1]


def test_t1_respuesta_sin_archivos_se_repara():
    validador = ValidadorSimulado()
    ag, fn = agente([json.dumps(SPEC), "No puedo ayudar", CODIGO_OK], validador)
    res = ag.correr(ESCENARIO, "T1")
    assert res.intentos_detalle[0].control_fallido == "formato"
    assert res.intentos == 1 and res.validate_ok


def test_t0_una_sola_llamada_sin_reparar():
    validador = ValidadorSimulado(regla=lambda a: resultado_error(a, "validate", "Invalid block"))
    ag, fn = agente([CODIGO], validador)
    res = ag.correr(ESCENARIO, "T0")
    assert len(fn.prompts) == 1
    assert "Esquema del proveedor" not in fn.prompts[0]
    assert res.intentos == 0 and not res.validate_ok
    assert len(validador.llamadas) == 1


def test_error_del_llm_es_entorno():
    def falla(**kwargs):
        raise TimeoutError("sin red")
    llm = ClienteLLM("m", 0.75, 3.75, completion_fn=falla, espera_s=0, reintentos=2)
    ag = Agente(llm, ValidadorSimulado(), Catalogo.cargar(), Config())
    res = ag.correr(ESCENARIO, "T1")
    assert res.tipo_error == "entorno"
    assert "sin red" in res.error_final


def test_fila_csv_tiene_campos_del_anexo_c():
    ag, _ = agente([json.dumps(SPEC), CODIGO_OK], ValidadorSimulado())
    fila = ag.correr(ESCENARIO, "T1").fila_csv()
    for campo in ["run_id", "escenario_id", "tratamiento", "validate_ok", "plan_ok", "intentos",
                  "checkov_fallidas", "cobertura", "tiempo_s", "tokens_entrada", "tokens_salida", "costo_usd",
                  "tipo_error"]:
        assert campo in fila
