# AVA-01: Plataforma de reservas con datos, archivos y secretos privados
# Escrito a mano por Paolo Ramírez Melendres (medición manual)

# 1. Red privada (VPC), subred y firewall interno
resource "google_compute_network" "vpc" {
  name                    = "vpc-reservas"
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "subred" {
  name          = "subred-reservas"
  ip_cidr_range = "10.10.1.0/24"
  region        = var.region
  network       = google_compute_network.vpc.id
}

resource "google_compute_firewall" "internal_only" {
  name    = "fw-allow-internal-reservas"
  network = google_compute_network.vpc.name

  allow {
    protocol = "tcp"
    ports    = ["0-65535"]
  }
  allow {
    protocol = "udp"
    ports    = ["0-65535"]
  }
  allow {
    protocol = "icmp"
  }

  source_ranges = [google_compute_subnetwork.subred.ip_cidr_range]
}

# 2. Conexión de servicios privados (VPC Peering para Cloud SQL)
resource "google_compute_global_address" "private_ip_alloc" {
  name          = "ip-alloc-sql-reservas"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = google_compute_network.vpc.id
}

resource "google_service_networking_connection" "private_vpc_connection" {
  network                 = google_compute_network.vpc.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_ip_alloc.name]
}

# 3. Base de datos Cloud SQL PostgreSQL privada
resource "google_sql_database_instance" "db" {
  name                = "db-reservas-dev"
  database_version    = "POSTGRES_16"
  region              = var.region
  deletion_protection = false

  depends_on = [google_service_networking_connection.private_vpc_connection]

  settings {
    tier    = "db-f1-micro"
    edition = "ENTERPRISE"

    ip_configuration {
      ipv4_enabled    = false
      private_network = google_compute_network.vpc.id
    }
  }
}

resource "google_sql_database" "reservas" {
  name     = "reservas"
  instance = google_sql_database_instance.db.name
}

resource "google_sql_user" "reservas_user" {
  name     = "reservas_user"
  instance = google_sql_database_instance.db.name
  password = var.db_password
}

# 4. Almacenamiento seguro para comprobantes (Bucket privado)
resource "google_storage_bucket" "comprobantes" {
  name                        = "${var.project_id}-comprobantes-reservas"
  location                    = var.region
  force_destroy               = true
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
}

# 5. Secret Manager para contraseñas y claves de API
resource "google_secret_manager_secret" "db_password" {
  secret_id = "reservas-db-password"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "db_password" {
  secret      = google_secret_manager_secret.db_password.id
  secret_data = var.db_password
}

resource "google_secret_manager_secret" "pasarela_key" {
  secret_id = "reservas-pasarela-key"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "pasarela_key" {
  secret      = google_secret_manager_secret.pasarela_key.id
  secret_data = var.payment_api_key
}

# 6. Cuenta de servicio e IAM con principio de menor privilegio
resource "google_service_account" "sa_api" {
  account_id   = "sa-api-reservas"
  display_name = "Cuenta de servicio API de Reservas"
}

resource "google_project_iam_member" "sql_client" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.sa_api.email}"
}

resource "google_project_iam_member" "secret_accessor" {
  project = var.project_id
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:${google_service_account.sa_api.email}"
}

resource "google_project_iam_member" "storage_user" {
  project = var.project_id
  role    = "roles/storage.objectUser"
  member  = "serviceAccount:${google_service_account.sa_api.email}"
}

# 7. Cloud Run v2 con Direct VPC egress y secretos inyectados
resource "google_cloud_run_v2_service" "api" {
  name                = "api-reservas"
  location            = var.region
  deletion_protection = false
  ingress             = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.sa_api.email

    vpc_access {
      network_interfaces {
        network    = google_compute_network.vpc.id
        subnetwork = google_compute_subnetwork.subred.id
      }
      egress = "PRIVATE_RANGES_ONLY"
    }

    containers {
      image = var.container_image
      ports {
        container_port = var.puerto
      }

      env {
        name  = "DB_HOST"
        value = google_sql_database_instance.db.private_ip_address
      }
      env {
        name  = "DB_NAME"
        value = google_sql_database.reservas.name
      }
      env {
        name  = "BUCKET_COMPROBANTES"
        value = google_storage_bucket.comprobantes.name
      }
      env {
        name = "DB_PASSWORD"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.db_password.secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "PAYMENT_API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.pasarela_key.secret_id
            version = "latest"
          }
        }
      }
    }
  }

  depends_on = [
    google_project_iam_member.sql_client,
    google_project_iam_member.secret_accessor,
    google_project_iam_member.storage_user,
    google_secret_manager_secret_version.db_password,
    google_secret_manager_secret_version.pasarela_key,
  ]
}

resource "google_cloud_run_v2_service_iam_member" "invoker" {
  name     = google_cloud_run_v2_service.api.name
  location = google_cloud_run_v2_service.api.location
  role     = "roles/run.invoker"
  member   = "allUsers"
}
