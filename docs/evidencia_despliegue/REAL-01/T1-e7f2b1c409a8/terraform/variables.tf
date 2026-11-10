variable "project_id" {
  type        = string
  description = "ID del proyecto de GCP"
}

variable "region" {
  type    = string
  default = "southamerica-west1"
}

variable "imagen_api" {
  type    = string
  default = "southamerica-west1-docker.pkg.dev/PROYECTO/apps/picanteria-api:v1"
}

variable "imagen_web" {
  type    = string
  default = "southamerica-west1-docker.pkg.dev/PROYECTO/apps/picanteria-web:v1"
}

variable "db_password" {
  type      = string
  sensitive = true
}

variable "admin_password" {
  type      = string
  sensitive = true
}
