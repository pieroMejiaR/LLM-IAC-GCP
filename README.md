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

### Lote del experimento

`eval/run_batch.py` corre todos los escenarios de `eval/scenarios/` con T0 y T1, 3 repeticiones cada uno (72 corridas), en orden aleatorio con semilla fija. Cada corrida agrega su fila a `resultados/runs.csv` apenas termina y guarda sus archivos como `agent generar`. Si el lote se corta, `--reanudar` salta las corridas que ya están en el CSV.

```bash
python -m eval.run_batch --solo-orden     # muestra el orden sin llamar al LLM
python -m eval.run_batch                  # lote completo; --solo BAS-01 --repeticiones 1 para una prueba corta
python -m eval.run_batch --reanudar
docker compose run --rm --entrypoint python agente -m eval.run_batch
```

### Análisis estadístico

`eval/analysis.py` lee `runs.csv` y escribe `analisis.md` (tablas 5.1 a 5.4), `analisis.json` y `por_escenario.csv` en `<carpeta del CSV>/analisis/`. Compara T1 con T0 con tres métodos: Wilcoxon de rangos con signo pareado por escenario (promedio de las repeticiones, p exacto y ajuste de Holm entre métricas), McNemar para validate y plan con los pares de la misma repetición (p exacto y χ² con corrección de continuidad), e IC 95 % por bootstrap remuestreando escenarios (B = 10 000, semilla fija). Excluye las corridas con `tipo_error = entorno` y, si una corrida se repitió, usa su última fila. Solo usa la biblioteca estándar.

`eval/simulacion.py` genera un `runs.csv` inventado para probar el análisis sin gastar en el LLM; esos datos no van al informe.

```bash
python -m eval.analysis resultados/runs.csv
python -m eval.simulacion && python -m eval.analysis resultados/simulado/runs.csv
python -m eval.simulacion --efecto nulo --salida resultados/simulado-nulo/runs.csv   # T1 igual a T0
```

## Sandbox en Docker

Es la forma de correr el experimento y el despliegue. Requiere Docker con Compose v2 y haber ejecutado `gcloud auth application-default login`. En Windows la credencial se encuentra sola; en Linux o macOS se indica con `ADC_HOST` en `.env`.

```bash
docker compose build                    # ~1,6 GB: Terraform, proveedores, TFLint y Checkov
docker compose run --rm pruebas         # pytest dentro del contenedor, con Terraform real y sin LLM ni GCP
docker compose run --rm agente generar eval/scenarios/BAS-01.yaml --tratamiento T1
docker compose run --rm despliegue desplegar casos/caso_real.yaml
```

`eval/` y `casos/` se montan de solo lectura (los escenarios nuevos no requieren reconstruir la imagen) y los resultados quedan en `resultados/`.

### Interfaz web

```bash
docker compose up -d web-puente              # modo evaluación: http://127.0.0.1:8787 (otro puerto: WEB_PUERTO en .env)
docker compose up -d web-puente-despliegue   # con despliegue:  http://127.0.0.1:8788 (WEB_DESPLIEGUE_PUERTO)
docker compose down                          # las apaga
docker compose up -d --force-recreate web web-despliegue   # tras cambiar .env o hacer el login de ADC (leen la configuración al arrancar)
```

Es un chat: la persona escribe el requerimiento, revisa la especificación (la aprueba o pide cambios, y cada ronda es una revisión A, B, C…), y el agente genera, valida y repara con el mismo bucle T1 del experimento (`Agente.iniciar`, `especificar` y `completar`, que también usa `correr`). Muestra la línea de controles de cada intento, los hallazgos de Checkov, el costo y los archivos, que se descargan en un `.zip`.

Hay dos servicios con la misma página:

- **`web` (modo evaluación, puerto 8787). Nunca despliega**: no recibe un desplegador y sus rutas de despliegue responden 403. Usa la red y el proxy de evaluación y la cuenta Viewer.
- **`web-despliegue` (puerto 8788)**, iniciado con `web --despliegue` en la red y con el proxy del modo despliegue. Si el código pasó todos los controles, incluido `plan` con la cuenta Viewer, la página ofrece desplegarlo con el mismo flujo de `desplegar` (`agent/deploy.py`, sin cambios en sus controles): pide las variables sin valor por defecto (contraseñas, que solo viven en memoria), calcula un plan guardado con la cuenta de despliegue, lo rechaza si trae recursos fuera del catálogo o acciones de borrado (S8), lo aplica solo si la persona escribe el ID del proyecto (S7), prueba `service_url` y pide una segunda confirmación para destruir. Sin respuesta en 30 minutos no se aplica nada. La evidencia queda en `resultados/web/<corrida>/evidencia/` y, si los recursos se conservan, se destruyen después con `docker compose run --rm despliegue destruir resultados/web/<corrida>/despliegue`.

**Carpeta de la aplicación (opcional).** Para que la persona no tenga que escribir datos técnicos, puede adjuntar la carpeta de su aplicación. El navegador lee solo archivos de configuración conocidos (`Dockerfile`, `docker-compose.yml`, `.env.example`, `package.json`, la plantilla de nginx y `schema.prisma`) y `agent/proyecto.py` arma con ellos una ficha técnica: componentes, puertos, variables, base de datos y los comandos exactos para publicar cada imagen. La ficha acompaña al requerimiento como texto, de modo que el prompt de análisis es el mismo del experimento. Es determinista (no usa el modelo) y nunca lee `.env` ni deja pasar valores de variables que parezcan secretas; el navegador los quita antes de enviar y el servidor repite el control. El contenido del proyecto se trata como dato, y el código generado pasa por los mismos controles y por la aprobación humana.

**Si la aplicación está en la computadora de quien despliega**, antes hay que publicar su imagen en Artifact Registry: Cloud Run solo ejecuta imágenes que estén en el registro. El agente no puede hacerlo, porque corre en un sandbox sin Docker ni internet, así que el paso se hace en esa máquina. La interfaz de despliegue lo guía (arma los comandos `docker build` y `docker push` con el repositorio del proyecto y, antes de planificar, lista las imágenes que usa el código), y el script lo resuelve en un comando:

```powershell
.\scripts\publicar-imagenes.ps1 -Carpeta E:\ruta\a\la\aplicacion -Imagen picanteria-api=apps/api/Dockerfile,picanteria-web=apps/web/Dockerfile
.\scripts\publicar-imagenes.ps1 -Carpeta C:\mi-app -Imagen mi-app=Dockerfile -Simular   # solo muestra los comandos
```

Las corridas de la interfaz se guardan en `resultados/web/`, fuera de los escenarios del experimento. El servidor usa solo la biblioteca estándar (`agent/web/servidor.py`), atiende un trabajo con el LLM a la vez, solo responde a `localhost` y del mismo origen, y la página inserta el texto del modelo con `textContent`. Los servicios `web` y `web-despliegue` quedan en sus redes internas, como el agente; los puentes `web-puente` y `web-puente-despliegue` (nginx sin privilegios, imagen fijada por digest, `sandbox/puente/`) son las únicas piezas con puerto publicado, solo en 127.0.0.1, y no ejecutan código del agente.

| Capa | Qué hace |
| --- | --- |
| Revisión del código | `sandbox/guard.py` analiza el HCL antes de `init` y rechaza otros proveedores, `provisioner`, `data "external"` y `"http"`, módulos, `backend`, `import`, funciones que leen archivos (`file`, `templatefile`…), fuentes de datos que exponen tokens y argumentos del provider que cambian la credencial o los endpoints. También rechaza variables `sensitive = true` con `default`, para que no queden contraseñas ni claves escritas en el código. Si no puede analizar un archivo, lo rechaza (S2 a S4). |
| Valores de relleno | En modo evaluación nadie entrega secretos, así que `plan` recibe un valor de relleno (`TF_VAR_*`) para cada variable sin `default`; así mide el código y no la falta de valores. En modo despliegue los valores son reales. Se agregó tras el piloto (`lote-v1`), donde T1 pasaba `plan` escribiendo contraseñas por defecto. |
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

### Diagnóstico del entorno

Antes de S5, de la corrida del experimento o del despliegue, quien tiene las credenciales ejecuta:

```bash
docker compose run --rm --entrypoint python agente -m sandbox.pruebas_seguridad diagnostico
```

Revisa las variables de `.env`, la credencial montada, la salida por el proxy, la clave y el modelo de Gemini, la suplantación de las cuentas de evaluación y despliegue (con un `terraform plan` que solo lee el proyecto) y el tiempo de una validación completa. No muestra la clave ni tokens, así que el archivo que deja en `resultados/evidencia/` se puede compartir con el equipo.

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
| `agent/cli.py` | CLI: `generar`, `desplegar`, `destruir` y `web` | Piero |
| `agent/web/` | Interfaz web (chat) del modo evaluación | Piero |
| `sandbox/puente/` | nginx que publica la interfaz web en 127.0.0.1 | Piero; revisa Santiago |
| `eval/` | Escenarios y ejecución por lotes | Gabriel |

## Cuentas de servicio

- `GCP_CUENTA_EVALUACION`: rol `roles/viewer`. El agente la suplanta con `GOOGLE_IMPERSONATE_SERVICE_ACCOUNT` solo para `plan`.
- `GCP_CUENTA_DESPLIEGUE`: roles limitados a los servicios del caso (por ejemplo, `roles/run.admin`, `roles/iam.serviceAccountUser` y los que el caso requiera). Solo la usa `agent desplegar`.

Quien ejecuta necesita `roles/iam.serviceAccountTokenCreator` sobre ambas cuentas.
