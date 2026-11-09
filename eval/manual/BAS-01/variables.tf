variable "project_id" {
  type        = string
  description = "ID del proyecto en Google Cloud"
}

variable "region" {
  type        = string
  description = "Región de despliegue"
  default     = "southamerica-west1"
}
