Reglas del proyecto Terraform:
- Usa solo los proveedores hashicorp/google y hashicorp/google-beta. No uses random, null, http, external, local, time, tls ni otros.
- En versions.tf declara el bloque terraform con required_version = ">= 1.5" y required_providers con google = { source = "hashicorp/google", version = "$version_proveedor" }. Agrega google-beta solo si lo usas.
- En providers.tf configura provider "google" con project = var.project_id y region = var.region.
- En variables.tf declara project_id (string, sin valor por defecto) y region (string, valor por defecto "$region"), además de las variables que necesites.
- No declares backend ni bloque cloud: el estado es local.
- No uses provisioners, módulos remotos ni bloques data "external" o "http".
- No crees recursos google_project_service: las APIs ya están habilitadas.
- No escribas contraseñas ni valores secretos en el código: decláralos como variables con sensitive = true.
- Si hay un servicio de Cloud Run, exporta su URL en outputs.tf con el nombre service_url.
- Permite destruir los recursos: si un recurso admite deletion_protection, defínelo en false.

Formato de respuesta: escribe cada archivo completo entre etiquetas, sin ningún texto fuera de ellas:
<file name="main.tf">
...contenido...
</file>
Archivos esperados: versions.tf, providers.tf, main.tf, variables.tf y outputs.tf.
