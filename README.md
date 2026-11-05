# Agente IaC GCP

Agente basado en LLM que convierte requerimientos en lenguaje natural en código Terraform para Google Cloud, lo valida (`fmt`, `validate`, TFLint, Checkov y `plan`) y lo repara de forma iterativa. Tiene dos modos:

- **Evaluación** (experimento T0 frente a T1): nunca despliega. `plan` corre con una cuenta de servicio Viewer.
- **Despliegue** (un caso real): el agente aplica un plan guardado solo después de que una persona escribe el ID del proyecto, y destruye los recursos con una segunda confirmación.

## Instalación

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env          # completar GEMINI_API_KEY, GCP_PROYECTO y las cuentas de servicio
pytest                        # sin LLM ni Terraform; las pruebas del contenedor se omiten
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

## Sandbox en Docker

Es la forma de correr el experimento y el despliegue. Requiere Docker con Compose v2 y haber ejecutado `gcloud auth application-default login`. En Windows la credencial se encuentra sola; en Linux o macOS se indica con `ADC_HOST` en `.env`.

```bash
docker compose build                    # ~1,6 GB: Terraform, proveedores, TFLint y Checkov
docker compose run --rm pruebas         # pytest dentro del contenedor, con Terraform real y sin LLM ni GCP
docker compose run --rm agente generar eval/scenarios/BAS-01.yaml --tratamiento T1
docker compose run --rm despliegue desplegar casos/caso_real.yaml
```

`eval/` y `casos/` se montan de solo lectura (los escenarios nuevos no requieren reconstruir la imagen) y los resultados quedan en `resultados/`.

| Capa | Qué hace |
| --- | --- |
| Revisión del código | `sandbox/guard.py` analiza el HCL antes de `init` y rechaza otros proveedores, `provisioner`, `data "external"` y `"http"`, módulos, `backend`, `import`, funciones que leen archivos (`file`, `templatefile`…), fuentes de datos que exponen tokens y argumentos del provider que cambian la credencial o los endpoints. Si no puede analizar un archivo, lo rechaza (S2 a S4). |
| Proveedores | Terraform solo instala google y google-beta desde un espejo dentro de la imagen (`sandbox/terraform.rc`); no llega al registro. |
| Red | El agente está en una red interna de Docker, sin ruta a internet. Su única salida es un proxy Squid que solo abre túneles HTTPS a `*.googleapis.com`, y en despliegue también a `*.run.app` (`sandbox/proxy/permitidos_*.txt`) (S6). |
| Proceso | Usuario sin privilegios (uid 10001), sistema de archivos de solo lectura, `/tmp` en memoria, sin capabilities, `no-new-privileges` y límites de memoria, CPU y procesos. |
| Credenciales | La credencial se monta de solo lectura en `/run/secrets/adc`. `plan` suplanta la cuenta Viewer (S5). La clave del LLM no llega a Terraform, TFLint ni Checkov. |

Versiones fijas de la imagen:

| Herramienta | Versión | Dónde se fija |
| --- | --- | --- |
| Terraform | 1.16.7 | `Dockerfile` (`TERRAFORM_VERSION`) |
| Proveedores google y google-beta | 8.8.1 | `Dockerfile` (`GOOGLE_PROVIDER_VERSION`) |
| TFLint / ruleset google | 0.65.0 / 0.40.0 | `Dockerfile` / `sandbox/.tflint.hcl` |
| Checkov | 3.3.30 | `sandbox/requisitos/checkov.lock` |
| Python / LiteLLM | 3.12.15 / 1.105.3 | `Dockerfile` / `sandbox/requisitos/agente.lock` |

Los `.lock` de Python llevan hashes. Para cambiar una dependencia, edite el `.in` y regenere:

```bash
docker run --rm -v "$PWD/sandbox/requisitos:/w" -w /w python:3.12.15-slim-trixie sh -c "pip install -q pip-tools==7.5.1 && \
  pip-compile -q --generate-hashes --allow-unsafe --strip-extras --no-header -o agente.lock agente.in && \
  pip-compile -q --generate-hashes --allow-unsafe --strip-extras --no-header -o checkov.lock checkov.in"
```

El esquema del grounding (`grounding/esquema_catalogo.json`) se genera dentro de la imagen con la misma versión del proveedor que valida.

### Evidencia de seguridad

`pytest` termina con un resumen de las pruebas S1 a S8. S5 y S6 tienen además su propia evidencia, que se guarda en `resultados/evidencia/`:

```bash
docker compose run --rm --entrypoint python agente -m sandbox.pruebas_seguridad s6         # red
docker compose run --rm --entrypoint python agente -m sandbox.pruebas_seguridad s5         # cuenta Viewer
docker compose run --rm --entrypoint python agente -m sandbox.pruebas_seguridad versiones  # sección 4.1
docker compose logs --no-log-prefix proxy                                                  # cada conexión permitida o rechazada
```

En Linux, si la credencial tiene permisos 0600, agregue `--user "$(id -u):$(id -g)"` a `docker compose run`.

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
| `sandbox/local_runner.py` | Ejecutor de Terraform, TFLint y Checkov (en el contenedor o en el PATH) | Santiago |
| `sandbox/guard.py` | Revisión estática del HCL previa a `init` | Santiago |
| `Dockerfile`, `compose.yaml`, `sandbox/proxy/` | Imagen del sandbox, red aislada y proxy de salida | Santiago |
| `sandbox/pruebas_seguridad.py` | Evidencia de S5, S6 y versiones | Santiago |
| `agent/cli.py` | CLI mínima | Paolo la amplía |
| `eval/` | Escenarios y ejecución por lotes | Gabriel |

## Cuentas de servicio

- `GCP_CUENTA_EVALUACION`: rol `roles/viewer`. El agente la suplanta con `GOOGLE_IMPERSONATE_SERVICE_ACCOUNT` solo para `plan`.
- `GCP_CUENTA_DESPLIEGUE`: roles limitados a los servicios del caso (por ejemplo, `roles/run.admin`, `roles/iam.serviceAccountUser` y los que el caso requiera). Solo la usa `agent desplegar`.

Quien ejecuta necesita `roles/iam.serviceAccountTokenCreator` sobre ambas cuentas.
