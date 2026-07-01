# Reuses the existing "trading" BigQuery dataset created by the MR repo's
# Terraform stack (infra/bigquery.tf there) — same GCP project, shared
# dataset, Momentum-only tables. Referenced via data source, not created
# here, to avoid a duplicate-resource conflict between the two Terraform
# states.
data "google_bigquery_dataset" "trading" {
  dataset_id = var.bq_dataset
  project    = var.project_id
}

# ── Momentum Weights (per-asset target/current weight per rebalance) ──

resource "google_bigquery_table" "momentum_weights" {
  dataset_id          = data.google_bigquery_dataset.trading.dataset_id
  table_id            = "momentum_weights"
  project             = var.project_id
  deletion_protection = false

  time_partitioning {
    type  = "DAY"
    field = "timestamp"
  }

  clustering = ["account", "symbol"]

  schema = jsonencode([
    { name = "timestamp", type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "account", type = "STRING", mode = "NULLABLE" },
    { name = "symbol", type = "STRING", mode = "REQUIRED" },
    { name = "variant", type = "STRING", mode = "NULLABLE" },
    { name = "target_weight", type = "FLOAT64", mode = "NULLABLE" },
    { name = "current_weight", type = "FLOAT64", mode = "NULLABLE" },
    { name = "weight_delta", type = "FLOAT64", mode = "NULLABLE" },
    { name = "direction", type = "STRING", mode = "NULLABLE" },
    { name = "position_value", type = "FLOAT64", mode = "NULLABLE" },
  ])
}

# ── Momentum Rebalances (daily rebalance run summary) ──

resource "google_bigquery_table" "momentum_rebalances" {
  dataset_id          = data.google_bigquery_dataset.trading.dataset_id
  table_id            = "momentum_rebalances"
  project             = var.project_id
  deletion_protection = false

  time_partitioning {
    type  = "DAY"
    field = "timestamp"
  }

  clustering = ["account"]

  schema = jsonencode([
    { name = "timestamp", type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "account", type = "STRING", mode = "NULLABLE" },
    { name = "variant", type = "STRING", mode = "NULLABLE" },
    { name = "n_long", type = "INT64", mode = "NULLABLE" },
    { name = "n_short", type = "INT64", mode = "NULLABLE" },
    { name = "n_flat", type = "INT64", mode = "NULLABLE" },
    { name = "gross_exposure", type = "FLOAT64", mode = "NULLABLE" },
    { name = "net_exposure", type = "FLOAT64", mode = "NULLABLE" },
    { name = "n_orders", type = "INT64", mode = "NULLABLE" },
    { name = "turnover", type = "FLOAT64", mode = "NULLABLE" },
    { name = "duration_s", type = "FLOAT64", mode = "NULLABLE" },
  ])
}

# ── PnL Snapshots (shared shape with MR's pnl_snapshots, momentum-scoped) ──

resource "google_bigquery_table" "momentum_pnl_snapshots" {
  dataset_id          = data.google_bigquery_dataset.trading.dataset_id
  table_id            = "momentum_pnl_snapshots"
  project             = var.project_id
  deletion_protection = false

  time_partitioning {
    type  = "DAY"
    field = "timestamp"
  }

  clustering = ["account"]

  schema = jsonencode([
    { name = "timestamp", type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "account", type = "STRING", mode = "NULLABLE" },
    { name = "cash", type = "FLOAT64", mode = "NULLABLE" },
    { name = "total_equity", type = "FLOAT64", mode = "NULLABLE" },
    { name = "commission_total", type = "FLOAT64", mode = "NULLABLE" },
    { name = "unrealized_pnl", type = "FLOAT64", mode = "NULLABLE" },
    { name = "realized_pnl", type = "FLOAT64", mode = "NULLABLE" },
  ])
}

# ── Alerts (shared shape with MR's alerts, momentum-scoped) ──

resource "google_bigquery_table" "momentum_alerts" {
  dataset_id          = data.google_bigquery_dataset.trading.dataset_id
  table_id            = "momentum_alerts"
  project             = var.project_id
  deletion_protection = false

  time_partitioning {
    type  = "DAY"
    field = "timestamp"
  }

  clustering = ["alert_type", "account"]

  schema = jsonencode([
    { name = "timestamp", type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "account", type = "STRING", mode = "NULLABLE" },
    { name = "alert_type", type = "STRING", mode = "NULLABLE" },
    { name = "severity", type = "STRING", mode = "NULLABLE" },
    { name = "message", type = "STRING", mode = "NULLABLE" },
  ])
}
