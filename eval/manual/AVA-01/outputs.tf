output "service_url" {
  description = "URL pública del servicio Cloud Run de reservas"
  value       = google_cloud_run_v2_service.api.uri
}

output "db_instance_name" {
  description = "Nombre de la instancia Cloud SQL"
  value       = google_sql_database_instance.db.name
}

output "bucket_name" {
  description = "Nombre del bucket de comprobantes"
  value       = google_storage_bucket.comprobantes.name
}
