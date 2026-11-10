output "service_url" {
  description = "URL pública de la web"
  value       = google_cloud_run_v2_service.web.uri
}

output "api_url" {
  value = google_cloud_run_v2_service.api.uri
}
