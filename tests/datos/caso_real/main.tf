# Caso real REAL-01 (picantería) tal como lo generaría el agente con las notas del catálogo.
resource "google_service_account" "run" {
  account_id   = "picanteria-run"
  display_name = "Cuenta de Cloud Run de la picantería"
}

resource "google_project_iam_member" "sql_client" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.run.email}"
}

resource "google_project_iam_member" "secret_accessor" {
  project = var.project_id
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:${google_service_account.run.email}"
}

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

resource "google_sql_database" "picanteria" {
  name     = "picanteria"
  instance = google_sql_database_instance.db.name
}

resource "google_sql_user" "picanteria" {
  name     = "picanteria"
  instance = google_sql_database_instance.db.name
  password = var.db_password
}

resource "google_secret_manager_secret" "database_url" {
  secret_id = "picanteria-database-url"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "database_url" {
  secret      = google_secret_manager_secret.database_url.id
  secret_data = "postgresql://${google_sql_user.picanteria.name}:${var.db_password}@localhost/${google_sql_database.picanteria.name}?host=/cloudsql/${google_sql_database_instance.db.connection_name}"
}

resource "google_secret_manager_secret" "admin_password" {
  secret_id = "picanteria-admin-password"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "admin_password" {
  secret      = google_secret_manager_secret.admin_password.id
  secret_data = var.admin_password
}

resource "google_cloud_run_v2_service" "api" {
  name                = "picanteria-api"
  location            = var.region
  deletion_protection = false
  ingress             = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.run.email
    scaling {
      max_instance_count = 1
    }
    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [google_sql_database_instance.db.connection_name]
      }
    }
    containers {
      image = var.imagen_api
      ports {
        container_port = 3000
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
        name  = "COOKIE_SECURE"
        value = "true"
      }
      env {
        name  = "INITIAL_ADMIN_USERNAME"
        value = "admin"
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
      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }
    }
  }

  depends_on = [
    google_project_iam_member.sql_client,
    google_project_iam_member.secret_accessor,
    google_secret_manager_secret_version.database_url,
    google_secret_manager_secret_version.admin_password,
  ]
}

resource "google_cloud_run_v2_service" "web" {
  name                = "picanteria-web"
  location            = var.region
  deletion_protection = false
  ingress             = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.run.email
    scaling {
      max_instance_count = 1
    }
    containers {
      image = var.imagen_web
      ports {
        container_port = 80
      }
      env {
        name  = "API_URL"
        value = google_cloud_run_v2_service.api.uri
      }
    }
  }
}

resource "google_cloud_run_v2_service_iam_member" "api_publico" {
  name     = google_cloud_run_v2_service.api.name
  location = google_cloud_run_v2_service.api.location
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_cloud_run_v2_service_iam_member" "web_publico" {
  name     = google_cloud_run_v2_service.web.name
  location = google_cloud_run_v2_service.web.location
  role     = "roles/run.invoker"
  member   = "allUsers"
}
