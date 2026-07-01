resource "google_secret_manager_secret" "momentum_secrets" {
  for_each  = toset(var.secrets)
  secret_id = "${var.secret_prefix}-${each.value}"
  project   = var.project_id

  replication {
    auto {}
  }

  labels = {
    app = "aqm-momentum"
  }
}
