# INT-01: API de inventario con PostgreSQL privado
# Escrito a mano por Paolo Ramírez Melendres (medición manual)

# 1. Red privada (VPC) y subred
resource "google_compute_network" "vpc" {
  name                    = "vpc-inventario"
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "subred" {
  name          = "subred-inventario"
  ip_cidr_range = "10.0.1.0/24"
  region        = var.region
  network       = google_compute_network.vpc.id
}

# 2. Conexión de servicios privados (Private Services Access para Cloud SQL)
resource "google_compute_global_address" "private_ip_alloc" {
  name          = "ip-alloc-sql-inventario"
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

# 3. Instancia de Cloud SQL PostgreSQL privada
resource "google_sql_database_instance" "db" {
  name                = "db-inventario-dev"
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

resource "google_sql_database" "inventario" {
  name     = "inventario"
  instance = google_sql_database_instance.db.name
}

resource "google_sql_user" "inventario_user" {
  name     = "inventario_user"
  instance = google_sql_database_instance.db.name
  password = var.db_password
}

# 4. Cuenta de servicio e IAM con permisos mínimos
resource "google_service_account" "sa_api" {
  account_id   = "sa-api-inventario"
  display_name = "Cuenta de servicio API Inventario"
}

resource "google_project_iam_member" "sql_client" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.sa_api.email}"
}

# 5. Servicio de Cloud Run v2 con Direct VPC egress
resource "google_cloud_run_v2_service" "api" {
  name                = "api-inventario"
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
        value = google_sql_database.inventario.name
      }
      env {
        name  = "DB_USER"
        value = google_sql_user.inventario_user.name
      }
    }
  }
}

resource "google_cloud_run_v2_service_iam_member" "invoker" {
  name     = google_cloud_run_v2_service.api.name
  location = google_cloud_run_v2_service.api.location
  role     = "roles/run.invoker"
  member   = "allUsers"
}
