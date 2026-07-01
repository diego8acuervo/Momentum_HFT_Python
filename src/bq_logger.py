# -*- coding: utf-8 -*-
"""
Async BigQuery Logger for GCP deployment.
Thread-safe, buffered, with local CSV fallback on insert failure.
"""

import os
import csv
import time
import logging
import threading
from datetime import datetime, timezone
from collections import defaultdict

logger = logging.getLogger(__name__)


class BQLogger:
    """
    Non-blocking BigQuery streaming insert logger.

    Usage:
        bq = BQLogger("aqm-trading-prod", "trading")
        bq.log("fills", {"timestamp": "...", "account": "binance_live", ...})
        bq.shutdown()  # flush remaining on exit
    """

    FLUSH_INTERVAL_S = 10
    FLUSH_THRESHOLD = 100

    def __init__(self, project_id: str, dataset: str, credentials=None,
                 fallback_dir: str = "outputs"):
        self.project_id = project_id
        self.dataset = dataset
        self.fallback_dir = fallback_dir
        self._buffers = defaultdict(list)
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._client = None

        os.makedirs(fallback_dir, exist_ok=True)

        try:
            from google.cloud import bigquery
            if credentials:
                self._client = bigquery.Client(
                    project=project_id, credentials=credentials
                )
            else:
                self._client = bigquery.Client(project=project_id)
            logger.info("BQLogger: BigQuery client initialized for %s.%s",
                        project_id, dataset)
        except Exception as e:
            logger.warning("BQLogger: BigQuery unavailable (%s), using CSV fallback only", e)

        self._flush_thread = threading.Thread(
            target=self._flush_loop, daemon=True, name="bq-flush"
        )
        self._flush_thread.start()

    def log(self, table: str, row: dict) -> None:
        if "timestamp" not in row and "fill_time" not in row and "run_timestamp" not in row:
            row["timestamp"] = datetime.now(timezone.utc).isoformat()

        with self._lock:
            self._buffers[table].append(row)
            if len(self._buffers[table]) >= self.FLUSH_THRESHOLD:
                self._flush_table(table)

    def flush(self) -> None:
        with self._lock:
            for table in list(self._buffers.keys()):
                self._flush_table(table)

    def shutdown(self) -> None:
        self._stop_event.set()
        self.flush()
        self._flush_thread.join(timeout=5)
        logger.info("BQLogger: shutdown complete")

    def _flush_loop(self):
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=self.FLUSH_INTERVAL_S)
            self.flush()

    def _flush_table(self, table: str):
        rows = self._buffers.pop(table, [])
        if not rows:
            return

        if self._client:
            try:
                table_ref = f"{self.project_id}.{self.dataset}.{table}"
                errors = self._client.insert_rows_json(table_ref, rows)
                if errors:
                    logger.error("BQLogger: insert errors for %s: %s", table, errors[:3])
                    self._write_csv_fallback(table, rows)
                else:
                    logger.debug("BQLogger: flushed %d rows to %s", len(rows), table)
                return
            except Exception as e:
                logger.warning("BQLogger: BQ insert failed for %s (%s), falling back to CSV",
                               table, e)

        self._write_csv_fallback(table, rows)

    def _write_csv_fallback(self, table: str, rows: list):
        if not rows:
            return
        filepath = os.path.join(
            self.fallback_dir,
            f"bq_fallback_{table}_{datetime.now().strftime('%Y%m%d')}.csv"
        )
        file_exists = os.path.exists(filepath)
        try:
            with open(filepath, "a", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=rows[0].keys())
                if not file_exists:
                    writer.writeheader()
                writer.writerows(rows)
            logger.debug("BQLogger: wrote %d fallback rows to %s", len(rows), filepath)
        except Exception as e:
            logger.error("BQLogger: CSV fallback also failed for %s: %s", table, e)
