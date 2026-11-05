output "service_url" {
  value = google_cloud_run_v2_service.app.uri
}

output "sitio" {
  description = "Bucket del sitio // con barras y # numeral"
  value       = "https://storage.googleapis.com/${google_storage_bucket.sitio.name}/index.html"
}

output "descripcion" {
  value = trimspace(local.descripcion)
}
