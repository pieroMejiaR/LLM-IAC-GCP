variable "project_id" {
  type        = string
  description = "Proyecto de GCP (p. ej. mi-proyecto-123)"
}

variable "region" {
  type    = string
  default = "southamerica-west1"
}

variable "container_image" {
  type    = string
  default = "us-docker.pkg.dev/cloudrun/container/hello"
}

variable "db_password" {
  type      = string
  sensitive = true
  validation {
    condition     = length(var.db_password) >= 12
    error_message = "La contraseña debe tener al menos 12 caracteres; no uses file() para leerla."
  }
}

variable "api_key" {
  type      = string
  sensitive = true
}

variable "publico" {
  type    = bool
  default = true
}
