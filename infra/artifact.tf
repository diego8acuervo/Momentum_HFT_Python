# Reuses the existing "aqm-trading" Artifact Registry repo managed by the MR
# repo's Terraform stack (infra/artifact.tf there). Momentum only pushes a
# new image name ("momentum-hft") into it — no separate repo resource here
# to avoid a duplicate-resource conflict between the two Terraform states.
data "google_artifact_registry_repository" "trading" {
  location      = var.region
  repository_id = var.artifact_repo
  project       = var.project_id
}
