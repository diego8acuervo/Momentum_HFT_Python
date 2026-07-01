output "vm_external_ip" {
  description = "Static external IP of the momentum VM"
  value       = google_compute_address.momentum_ip.address
}

output "vm_instance_name" {
  description = "Name of the GCE instance"
  value       = google_compute_instance.momentum_vm.name
}

output "service_account_email" {
  description = "Service account email"
  value       = google_service_account.momentum.email
}

output "bigquery_dataset" {
  description = "BigQuery dataset ID (shared with MR)"
  value       = data.google_bigquery_dataset.trading.dataset_id
}

output "artifact_registry_repo" {
  description = "Artifact Registry repository path (shared with MR)"
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${data.google_artifact_registry_repository.trading.repository_id}"
}
