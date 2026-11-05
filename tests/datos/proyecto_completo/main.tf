locals {
  prefijo = "${var.project_id}-app"
  entornos = toset(["dev", "qa"])
  /* Comentario de bloque con llaves { y } y un provisioner "x" {}
     que no deben confundir la revisión. */
  descripcion = <<-EOT
    Sitio de ${var.project_id} # esto no es un comentario
    %{if var.publico}público%{else}privado%{endif}; llave suelta { y "comillas"
    literal $${no_interpola} y //tampoco
  EOT
  etiquetas = {
    "equipo"  = "tais"
    color     = "#00ff00"
    documento = "https://cloud.google.com/run/docs"
  }
}

# ----------------------------------------------------------------- IAM
resource "google_service_account" "app" {
  account_id   = "app-runner"
  display_name = "Cuenta de Cloud Run (${local.prefijo})"
}

resource "google_project_iam_member" "secretos" {
  project = var.project_id
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:${google_service_account.app.email}"
}

# ----------------------------------------------------------------- Secret Manager
resource "google_secret_manager_secret" "api" {
  secret_id = "api-key"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "api" {
  secret      = google_secret_manager_secret.api.id
  secret_data = var.api_key
}

# ----------------------------------------------------------------- Cloud Run
resource "google_cloud_run_v2_service" "app" {
  name                = "app"
  location            = var.region
  deletion_protection = false
  ingress             = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.app.email
    scaling {
      max_instance_count = 2
    }
    containers {
      image = var.container_image
      ports {
        container_port = 8080
      }
      dynamic "env" {
        for_each = { MODO = "dev", REGION = var.region }
        content {
          name  = env.key
          value = env.value
        }
      }
      env {
        name = "API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.api.secret_id
            version = "latest"
          }
        }
      }
    }
  }

  depends_on = [google_project_iam_member.secretos]
}

resource "google_cloud_run_v2_service_iam_member" "publico" {
  count    = var.publico ? 1 : 0
  provider = google
  name     = google_cloud_run_v2_service.app.name
  location = google_cloud_run_v2_service.app.location
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# ----------------------------------------------------------------- Cloud Storage
resource "google_storage_bucket" "sitio" {
  name                        = "${local.prefijo}-sitio"
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = true
  labels                      = { for k, v in local.etiquetas : k => replace(v, "/[^a-z0-9_-]/", "-") if k != "documento" }

  website {
    main_page_suffix = "index.html"
    not_found_page   = "404.html"
  }

  lifecycle {
    ignore_changes = [labels]
  }
}

moved {
  from = google_storage_bucket.web
  to   = google_storage_bucket.sitio
}

resource "google_storage_bucket" "por_entorno" {
  for_each                    = local.entornos
  name                        = "${local.prefijo}-${each.key}"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = true
}

resource "google_storage_bucket_iam_member" "lectura" {
  bucket = google_storage_bucket.sitio.name
  role   = "roles/storage.objectViewer"
  member = "allUsers"
}

check "sitio_existe" {
  data "google_storage_bucket" "sitio" {
    name = google_storage_bucket.sitio.name
  }
  assert {
    condition     = data.google_storage_bucket.sitio.uniform_bucket_level_access
    error_message = "El bucket ${google_storage_bucket.sitio.name} debe usar acceso uniforme."
  }
}

# ----------------------------------------------------------------- VPC
resource "google_compute_network" "vpc" {
  name                    = "vpc-app"
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "app" {
  name          = "subred-app"
  ip_cidr_range = "10.10.0.0/24"
  region        = var.region
  network       = google_compute_network.vpc.id
}

resource "google_compute_firewall" "interno" {
  name    = "permitir-interno"
  network = google_compute_network.vpc.name
  allow {
    protocol = "tcp"
    ports    = ["80", "443", "5432"]
  }
  source_ranges = [google_compute_subnetwork.app.ip_cidr_range]
}

# ----------------------------------------------------------------- Cloud SQL (IP privada)
resource "google_compute_global_address" "sql" {
  provider      = google-beta.beta
  name          = "rango-sql"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = google_compute_network.vpc.id
}

resource "google_service_networking_connection" "sql" {
  network                 = google_compute_network.vpc.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.sql.name]
}

resource "google_sql_database_instance" "db" {
  name                = "db-app"
  database_version    = "POSTGRES_16"
  region              = var.region
  deletion_protection = false

  settings {
    tier = "db-f1-micro"
    ip_configuration {
      ipv4_enabled    = false
      private_network = google_compute_network.vpc.id
    }
  }

  depends_on = [google_service_networking_connection.sql]
}

resource "google_sql_database" "app" {
  name     = "app"
  instance = google_sql_database_instance.db.name
}

resource "google_sql_user" "app" {
  name     = "app"
  instance = google_sql_database_instance.db.name
  password = var.db_password
}
