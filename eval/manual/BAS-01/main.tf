# BAS-01: Sitio estático público en Cloud Storage
# Escrito a mano por Paolo Ramírez Melendres (medición manual)

resource "google_storage_bucket" "sitio" {
  name                        = "${var.project_id}-sitio-estatico"
  location                    = var.region
  force_destroy               = true
  uniform_bucket_level_access = true

  website {
    main_page_suffix = "index.html"
    not_found_page   = "404.html"
  }
}

resource "google_storage_bucket_iam_member" "publico" {
  bucket = google_storage_bucket.sitio.name
  role   = "roles/storage.objectViewer"
  member = "allUsers"
}
