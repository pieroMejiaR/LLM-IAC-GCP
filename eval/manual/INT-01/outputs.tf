output "service_url" {
  description = "URL pública del servicio Cloud Run"
  value       = google_cloud_run_v2_service.api.uri
}

output "db_instance_name" {
  description = "Nombre de la instancia Cloud SQL"
  value       = google_sql_database_instance.db.name
}

output "db_private_ip" {
  description = "Dirección IP privada de Cloud SQL"
  value       = google_sql_database_instance.db.private_ip_address
}
