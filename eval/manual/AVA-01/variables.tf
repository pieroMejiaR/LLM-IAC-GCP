variable "project_id" {
  type        = string
  description = "ID del proyecto en Google Cloud"
}

variable "region" {
  type        = string
  description = "Región de despliegue"
  default     = "southamerica-west1"
}

variable "puerto" {
  type        = number
  description = "Puerto de escucha del contenedor"
  default     = 8080
}

variable "container_image" {
  type        = string
  description = "Imagen del contenedor de la API"
  default     = "southamerica-west1-docker.pkg.dev/PROYECTO/apps/reservas:v1"
}

variable "db_password" {
  type        = string
  description = "Contraseña para la base de datos PostgreSQL"
  sensitive   = true
}

variable "payment_api_key" {
  type        = string
  description = "Clave de API de la pasarela de pagos"
  sensitive   = true
}
