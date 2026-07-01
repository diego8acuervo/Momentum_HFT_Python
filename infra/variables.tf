variable "project_id" {
  description = "GCP project ID (shared with the MR trading stack)"
  type        = string
  default     = "aqm-trading-prod"
}

variable "region" {
  description = "GCP region"
  type        = string
  default     = "europe-west1"
}

variable "zone" {
  description = "GCP zone"
  type        = string
  default     = "europe-west1-b"
}

variable "vm_name" {
  description = "Name of the GCE VM"
  type        = string
  default     = "aqm-momentum-vm"
}

variable "machine_type" {
  description = "GCE machine type"
  type        = string
  default     = "e2-medium"
}

variable "bq_dataset" {
  description = "BigQuery dataset ID (reuses the existing MR 'trading' dataset in the same project)"
  type        = string
  default     = "trading"
}

variable "artifact_repo" {
  description = "Artifact Registry repository ID (reuses the existing MR repo, new image name)"
  type        = string
  default     = "aqm-trading"
}

variable "secret_prefix" {
  description = "Prefix for Secret Manager secrets (separate namespace from aqm-trading-*)"
  type        = string
  default     = "aqm-momentum"
}

variable "container_image" {
  description = "Docker image URI in Artifact Registry"
  type        = string
  default     = "europe-west1-docker.pkg.dev/aqm-trading-prod/aqm-trading/momentum-hft:latest"
}

variable "secrets" {
  description = "List of secret variable names to create in Secret Manager"
  type        = list(string)
  default = [
    "MOMENTUM_API_KEY",
    "MOMENTUM_SECRET_KEY",
  ]
}
