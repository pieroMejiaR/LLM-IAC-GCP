variable "project_id" {
  description = "ID del proyecto en Google Cloud"
  type        = string
}

variable "region" {
  description = "Región de Google Cloud para los recursos"
  type        = string
  default     = "southamerica-west1"
}

variable "db_password" {
  description = "Contraseña del usuario picanteria en Cloud SQL"
  type        = string
  sensitive   = true
}

variable "initial_admin_password" {
  description = "Contraseña inicial del administrador de la aplicación"
  type        = string
  sensitive   = true
}

variable "initial_admin_username" {
  description = "Nombre de usuario inicial del administrador de la aplicación"
  type        = string
  default     = "admin"
}

variable "api_container_image" {
  description = "Imagen de contenedor para la API"
  type        = string
  default     = "southamerica-west1-docker.pkg.dev/agente-iac-pm/apps/picanteria-api:v1"
}

variable "web_container_image" {
  description = "Imagen de contenedor para el frontend Web"
  type        = string
  default     = "southamerica-west1-docker.pkg.dev/agente-iac-pm/apps/picanteria-web:v1"
}
