terraform {
  required_version = ">= 1.5"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }

  backend "gcs" {
    bucket = "aqm-trading-tf-state"
    prefix = "terraform/momentum-state"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}
