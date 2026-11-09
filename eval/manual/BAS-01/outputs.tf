output "bucket_url" {
  description = "URL del bucket de almacenamiento"
  value       = google_storage_bucket.sitio.url
}

output "sitio_web_url" {
  description = "Endpoint web del sitio estático"
  value       = "https://storage.googleapis.com/${google_storage_bucket.sitio.name}/index.html"
}
