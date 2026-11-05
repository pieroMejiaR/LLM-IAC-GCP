# Sandbox del agente IaC GCP: el agente con Terraform, TFLint (ruleset google) y Checkov,
# con versiones fijas y los proveedores google y google-beta precargados.
#
#   docker compose build
#   docker compose run --rm agente generar eval/scenarios/BAS-01.yaml --tratamiento T1
#
# Las versiones de Terraform y del proveedor se fijan aquí; la del ruleset de TFLint en
# sandbox/.tflint.hcl y las de Python en sandbox/requisitos/*.lock (con hashes).

ARG PYTHON_IMAGEN=python:3.12.15-slim-trixie

# --------------------------------------------------------------------------- herramientas
FROM ${PYTHON_IMAGEN} AS herramientas
ARG TARGETARCH
ARG TERRAFORM_VERSION=1.16.7
ARG TFLINT_VERSION=0.65.0
ARG GOOGLE_PROVIDER_VERSION=8.8.1

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl unzip \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /descargas

# Terraform, verificado contra el SHA256SUMS de HashiCorp.
RUN curl -fsSLO "https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_linux_${TARGETARCH}.zip" \
 && curl -fsSLO "https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_SHA256SUMS" \
 && grep " terraform_${TERRAFORM_VERSION}_linux_${TARGETARCH}.zip\$" "terraform_${TERRAFORM_VERSION}_SHA256SUMS" | sha256sum -c - \
 && unzip -q "terraform_${TERRAFORM_VERSION}_linux_${TARGETARCH}.zip" terraform -d /usr/local/bin \
 && terraform version

# TFLint, verificado contra el checksums.txt del release.
RUN curl -fsSLO "https://github.com/terraform-linters/tflint/releases/download/v${TFLINT_VERSION}/tflint_linux_${TARGETARCH}.zip" \
 && curl -fsSLO "https://github.com/terraform-linters/tflint/releases/download/v${TFLINT_VERSION}/checksums.txt" \
 && grep " tflint_linux_${TARGETARCH}.zip\$" checksums.txt | sha256sum -c - \
 && unzip -q "tflint_linux_${TARGETARCH}.zip" tflint -d /usr/local/bin \
 && tflint --version

# Ruleset google de TFLint (tflint --init verifica la firma del plugin).
COPY sandbox/.tflint.hcl /app/sandbox/.tflint.hcl
RUN TFLINT_PLUGIN_DIR=/opt/tflint/plugins tflint --init --config /app/sandbox/.tflint.hcl \
 && TFLINT_PLUGIN_DIR=/opt/tflint/plugins tflint --version --config /app/sandbox/.tflint.hcl

# Espejo local de proveedores. terraform init descarga y verifica la firma de google y
# google-beta; el caché de plugins queda con el formato de un espejo "desempaquetado",
# del que init crea enlaces simbólicos en vez de copiar ~250 MB en cada corrida.
RUN mkdir -p /opt/terraform/plugins /precarga \
 && printf 'terraform {\n  required_providers {\n    google = {\n      source  = "hashicorp/google"\n      version = "%s"\n    }\n    google-beta = {\n      source  = "hashicorp/google-beta"\n      version = "%s"\n    }\n  }\n}\n' \
      "${GOOGLE_PROVIDER_VERSION}" "${GOOGLE_PROVIDER_VERSION}" > /precarga/versions.tf \
 && cd /precarga \
 && TF_PLUGIN_CACHE_DIR=/opt/terraform/plugins terraform init -input=false -no-color \
 && terraform providers schema -json > /precarga/esquema_completo.json

# --------------------------------------------------------------------------- checkov
FROM ${PYTHON_IMAGEN} AS checkov
COPY sandbox/requisitos/checkov.lock /tmp/checkov.lock
RUN python -m venv /opt/checkov \
 && /opt/checkov/bin/pip install --no-cache-dir --require-hashes -r /tmp/checkov.lock \
 && /opt/checkov/bin/checkov --version

# --------------------------------------------------------------------------- agente
FROM ${PYTHON_IMAGEN} AS agente
COPY sandbox/requisitos/agente.lock /tmp/agente.lock
RUN python -m venv /opt/agente \
 && /opt/agente/bin/pip install --no-cache-dir --require-hashes -r /tmp/agente.lock
COPY pyproject.toml /src/
COPY agent /src/agent
COPY grounding /src/grounding
COPY sandbox /src/sandbox
# --no-deps: todo viene del lock. pip check falla si pyproject.toml pide algo que el lock no trae.
RUN /opt/agente/bin/pip install --no-cache-dir --no-deps --no-build-isolation "/src[dev]" \
 && /opt/agente/bin/pip check

# Esquema del proveedor para el grounding, de la misma versión que valida el sandbox.
FROM agente AS esquema
COPY --from=herramientas /precarga/esquema_completo.json /tmp/esquema_completo.json
RUN mkdir /esquema \
 && cd /tmp \
 && /opt/agente/bin/python -m grounding.extraer_esquema /tmp/esquema_completo.json /esquema/esquema_catalogo.json

# --------------------------------------------------------------------------- imagen final
FROM ${PYTHON_IMAGEN}
LABEL org.opencontainers.image.title="agente-iac-gcp" \
      org.opencontainers.image.description="Agente LLM que genera y valida Terraform para GCP, en sandbox"

RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin agente

COPY --from=herramientas /usr/local/bin/terraform /usr/local/bin/tflint /usr/local/bin/
COPY --from=herramientas /opt/terraform /opt/terraform
COPY --from=herramientas /opt/tflint /opt/tflint
COPY --from=checkov /opt/checkov /opt/checkov
COPY --from=agente /opt/agente /opt/agente
RUN ln -s /opt/checkov/bin/checkov /usr/local/bin/checkov
COPY sandbox/terraform.rc /etc/terraform/terraform.rc

WORKDIR /app
COPY sandbox/.tflint.hcl sandbox/.tflint.hcl
COPY --from=esquema /esquema/esquema_catalogo.json grounding/esquema_catalogo.json
COPY eval eval
COPY casos casos
COPY tests tests
RUN mkdir resultados && chown agente resultados

# HOME en /tmp: el contenedor corre con el sistema de archivos de solo lectura y /tmp en tmpfs.
ENV PATH=/opt/agente/bin:$PATH \
    HOME=/tmp \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TF_CLI_CONFIG_FILE=/etc/terraform/terraform.rc \
    TF_IN_AUTOMATION=1 \
    CHECKPOINT_DISABLE=1 \
    TFLINT_PLUGIN_DIR=/opt/tflint/plugins \
    TFLINT_CONFIG=/app/sandbox/.tflint.hcl \
    BC_SKIP_MAPPING=TRUE \
    LITELLM_LOCAL_MODEL_COST_MAP=True \
    SANDBOX_CONTENEDOR=1

USER agente
ENTRYPOINT ["python", "-m", "agent"]
CMD ["--help"]
