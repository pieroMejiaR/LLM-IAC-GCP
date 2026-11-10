# ---------------------------------------------------------
# Cloud SQL: PostgreSQL 17
# ---------------------------------------------------------
resource "google_sql_database_instance" "db" {
  name                = "picanteria-db"
  database_version    = "POSTGRES_17"
  region              = var.region
  deletion_protection = false

  settings {
    tier    = "db-f1-micro"
    edition = "ENTERPRISE"
    ip_configuration {
      ipv4_enabled = true
    }
  }
}

resource "google_sql_database" "db" {
  name            = "picanteria"
  instance        = google_sql_database_instance.db.name
  deletion_policy = "ABANDON"
}

resource "google_sql_user" "user" {
  name            = "picanteria"
  instance        = google_sql_database_instance.db.name
  password        = var.db_password
  deletion_policy = "ABANDON"
}

# ---------------------------------------------------------
# Secret Manager: Secretos y Versiones
# ---------------------------------------------------------
resource "google_secret_manager_secret" "db_password" {
  secret_id = "secret-db-password"

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "db_password" {
  secret      = google_secret_manager_secret.db_password.id
  secret_data = var.db_password
}

resource "google_secret_manager_secret" "database_url" {
  secret_id = "secret-database-url"

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "database_url" {
  secret      = google_secret_manager_secret.database_url.id
  secret_data = "postgresql://${google_sql_user.user.name}:${urlencode(var.db_password)}@localhost/${google_sql_database.db.name}?host=/cloudsql/${google_sql_database_instance.db.connection_name}"
}

resource "google_secret_manager_secret" "admin_password" {
  secret_id = "secret-admin-password"

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "admin_password" {
  secret      = google_secret_manager_secret.admin_password.id
  secret_data = var.initial_admin_password
}

# ---------------------------------------------------------
# Cuentas de Servicio e IAM
# ---------------------------------------------------------
resource "google_service_account" "sa_api" {
  account_id   = "sa-api"
  display_name = "sa-api"
  description  = "Cuenta de servicio con minimo privilegio para la API"
}

resource "google_project_iam_member" "sa_api_cloudsql" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.sa_api.email}"
}

resource "google_project_iam_member" "sa_api_secrets" {
  project = var.project_id
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:${google_service_account.sa_api.email}"
}

resource "google_service_account" "sa_web" {
  account_id   = "sa-web"
  display_name = "sa-web"
  description  = "Cuenta de servicio con minimo privilegio para el frontend Web"
}

# ---------------------------------------------------------
# Cloud Run: API
# ---------------------------------------------------------
resource "google_cloud_run_v2_service" "api" {
  name                = "api"
  location            = var.region
  deletion_protection = false

  template {
    service_account = google_service_account.sa_api.email

    scaling {
      min_instance_count = 0
      max_instance_count = 2
    }

    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [google_sql_database_instance.db.connection_name]
      }
    }

    containers {
      image = var.api_container_image

      ports {
        container_port = 3000
      }

      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }

      env {
        name  = "COOKIE_SECURE"
        value = "true"
      }

      env {
        name  = "INITIAL_ADMIN_USERNAME"
        value = var.initial_admin_username
      }

      env {
        name = "DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.database_url.secret_id
            version = "latest"
          }
        }
      }

      env {
        name = "INITIAL_ADMIN_PASSWORD"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.admin_password.secret_id
            version = "latest"
          }
        }
      }
    }
  }

  depends_on = [
    google_secret_manager_secret_version.database_url,
    google_secret_manager_secret_version.admin_password,
    google_project_iam_member.sa_api_cloudsql,
    google_project_iam_member.sa_api_secrets
  ]
}

resource "google_cloud_run_v2_service_iam_member" "api_public" {
  name     = google_cloud_run_v2_service.api.name
  location = google_cloud_run_v2_service.api.location
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# ---------------------------------------------------------
# Cloud Run: Web
# ---------------------------------------------------------
resource "google_cloud_run_v2_service" "web" {
  name                = "web"
  location            = var.region
  deletion_protection = false

  template {
    service_account = google_service_account.sa_web.email

    scaling {
      min_instance_count = 0
      max_instance_count = 2
    }

    containers {
      image = var.web_container_image

      ports {
        container_port = 8080
      }

      env {
        name  = "API_URL"
        value = google_cloud_run_v2_service.api.uri
      }
    }
  }
}

resource "google_cloud_run_v2_service_iam_member" "web_public" {
  name     = google_cloud_run_v2_service.web.name
  location = google_cloud_run_v2_service.web.location
  role     = "roles/run.invoker"
  member   = "allUsers"
}
