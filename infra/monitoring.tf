resource "google_monitoring_uptime_check_config" "vm_uptime" {
  display_name = "AQM Momentum VM Uptime"
  project      = var.project_id
  timeout      = "10s"
  period       = "60s"

  monitored_resource {
    type = "gce_instance"
    labels = {
      instance_id = google_compute_instance.momentum_vm.instance_id
      project_id  = var.project_id
      zone        = var.zone
    }
  }

  tcp_check {
    port = 22
  }
}

resource "google_monitoring_alert_policy" "vm_cpu_high" {
  display_name = "AQM Momentum - High CPU"
  project      = var.project_id
  combiner     = "OR"

  conditions {
    display_name = "CPU > 85% for 10 min"
    condition_threshold {
      filter          = "resource.type = \"gce_instance\" AND metric.type = \"compute.googleapis.com/instance/cpu/utilization\" AND resource.labels.instance_id = \"${google_compute_instance.momentum_vm.instance_id}\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0.85
      duration        = "600s"

      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_MEAN"
      }
    }
  }

  alert_strategy {
    auto_close = "1800s"
  }
}

resource "google_monitoring_alert_policy" "vm_memory_high" {
  display_name = "AQM Momentum - High Memory"
  project      = var.project_id
  combiner     = "OR"

  conditions {
    display_name = "Memory > 90% for 5 min"
    condition_threshold {
      filter          = "resource.type = \"gce_instance\" AND metric.type = \"agent.googleapis.com/memory/percent_used\" AND resource.labels.instance_id = \"${google_compute_instance.momentum_vm.instance_id}\""
      comparison      = "COMPARISON_GT"
      threshold_value = 90
      duration        = "300s"

      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_MEAN"
      }
    }
  }

  alert_strategy {
    auto_close = "1800s"
  }
}
