terraform {
  required_version = ">= 1.5"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 6.0"
    }
    google-beta = {
      source  = "registry.terraform.io/hashicorp/google-beta"
      version = ">= 6.0"
    }
  }
}
