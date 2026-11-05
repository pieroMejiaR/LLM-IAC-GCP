# Proyecto de prueba del guard: debe pasar la revisión y terraform validate.
# Los comentarios mencionan a propósito provisioner "local-exec", data "external" y file("x").
provider "google" {
  project = var.project_id
  region  = var.region
  default_labels = {
    gestionado_por = "agente-iac"
  }
}

provider "google-beta" {
  alias   = "beta"
  project = var.project_id
  region  = var.region
}
