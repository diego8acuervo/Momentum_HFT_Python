resource "google_compute_address" "momentum_ip" {
  name    = "aqm-momentum-ip"
  region  = var.region
  project = var.project_id
}

resource "google_compute_instance" "momentum_vm" {
  name         = var.vm_name
  machine_type = var.machine_type
  zone         = var.zone
  project      = var.project_id

  boot_disk {
    initialize_params {
      image = "projects/cos-cloud/global/images/family/cos-stable"
      size  = 20
      type  = "pd-balanced"
    }
  }

  network_interface {
    network = "default"
    access_config {
      nat_ip = google_compute_address.momentum_ip.address
    }
  }

  metadata = {
    gce-container-declaration = yamlencode({
      spec = {
        containers = [{
          image = var.container_image
          env = [
            { name = "GCP_PROJECT_ID", value = var.project_id },
          ]
        }]
        restartPolicy = "Always"
      }
    })
  }

  service_account {
    email  = google_service_account.momentum.email
    scopes = ["cloud-platform"]
  }

  tags = ["aqm-momentum"]

  labels = {
    app         = "aqm-momentum"
    environment = "production"
  }

  allow_stopping_for_update = true
}
