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
  description = "Imagen de la API"
  default     = "southamerica-west1-docker.pkg.dev/PROYECTO/apps/inventario:v1"
}

variable "db_password" {
  type        = string
  description = "Contraseña del usuario de la base de datos"
  sensitive   = true
}
