# Agente IaC GCP

Agente basado en LLM que convierte requerimientos en lenguaje natural en código Terraform para Google Cloud, lo valida (`fmt`, `validate`, TFLint, Checkov y `plan`) y lo repara de forma iterativa. Tiene dos modos:

- **Evaluación** (experimento T0 frente a T1): nunca despliega. `plan` corre con una cuenta de servicio Viewer.
- **Despliegue** (un caso real): el agente aplica un plan guardado solo después de que una persona escribe el ID del proyecto, y destruye los recursos con una segunda confirmación.

## Instalación

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env          # completar GEMINI_API_KEY, GCP_PROYECTO y las cuentas de servicio
pytest                        # 40 pruebas, sin LLM ni Terraform
```

Requisitos para la validación real: Terraform, y opcionalmente TFLint (con el ruleset google) y Checkov en el PATH. Si TFLint o Checkov no están instalados, el control se marca como omitido.

## Uso

```bash
# Modo evaluación
python -m agent generar eval/scenarios/BAS-01.yaml --tratamiento T1
python -m agent generar eval/scenarios/BAS-01.yaml --tratamiento T0
python -m agent generar eval/scenarios/BAS-01.yaml --simulado   # prueba el LLM sin Terraform

# Modo despliegue (caso real)
python -m agent desplegar casos/caso_real.yaml
python -m agent destruir resultados/despliegue/REAL-01/T1-<run_id>/despliegue
```

Cada corrida deja en `resultados/<escenario>/<tratamiento>-<run_id>/` los archivos `.tf` y `resultado.json` con la traza completa (llamadas al LLM, tokens, costo, intentos y errores). `ResultadoCorrida.fila_csv()` devuelve la fila del Anexo C.

## Esquema del proveedor (grounding)

```bash
mkdir -p /tmp/esquema && cd /tmp/esquema
cat > versions.tf <<'EOF'
terraform {
  required_providers {
    google      = { source = "hashicorp/google" }
    google-beta = { source = "hashicorp/google-beta" }
  }
}
EOF
terraform init && terraform providers schema -json > esquema_completo.json
cd -
python -m grounding.extraer_esquema /tmp/esquema/esquema_completo.json grounding/esquema_catalogo.json
```

Sin este archivo el agente funciona, pero el grounding usa solo las notas del catálogo.

## Estructura

| Ruta | Contenido | Responsable |
| --- | --- | --- |
| `agent/pipeline.py` | Bucle analizar, planificar, generar, validar y reparar; T0 y T1 | Piero |
| `agent/llm.py` | Cliente LiteLLM, reintentos, tokens y costo | Piero |
| `agent/prompts/` | Plantillas de prompts | Piero |
| `agent/deploy.py` | Modo despliegue con aprobación, lista de recursos y prueba HTTP | Piero |
| `grounding/` | Catálogo de 6 servicios, índice del esquema del proveedor | Piero |
| `sandbox/contract.py` | Contrato entre el agente y el sandbox | Piero y Santiago |
| `sandbox/local_runner.py` | Ejecutor de referencia sin Docker | Santiago lo lleva al contenedor |
| `sandbox/guard.py` | Revisión estática mínima previa a `init` | Santiago la endurece |
| `agent/cli.py` | CLI mínima | Paolo la amplía |
| `eval/` | Escenarios y ejecución por lotes | Gabriel |

## Cuentas de servicio

- `GCP_CUENTA_EVALUACION`: rol `roles/viewer`. El agente la suplanta con `GOOGLE_IMPERSONATE_SERVICE_ACCOUNT` solo para `plan`.
- `GCP_CUENTA_DESPLIEGUE`: roles limitados a los servicios del caso (por ejemplo, `roles/run.admin`, `roles/iam.serviceAccountUser` y los que el caso requiera). Solo la usa `agent desplegar`.

Quien ejecuta necesita `roles/iam.serviceAccountTokenCreator` sobre ambas cuentas.
