output "service_url" {
  description = "URL pública del servicio frontend Web"
  value       = google_cloud_run_v2_service.web.uri
}
