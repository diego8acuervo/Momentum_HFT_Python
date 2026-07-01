# -*- coding: utf-8 -*-
"""
GCP Secret Manager adapter.
Loads secrets into os.environ so account_manager.py works unchanged.
Falls back to .env for local development.
"""

import os
import logging

logger = logging.getLogger(__name__)

DEFAULT_PREFIX = "aqm-trading"

EXPECTED_SECRETS = [
    "BINANCE_AQM_API_KEY",
    "BINANCE_AQM_SECRET_KEY",
    "BINANCE_TESTNET_API_KEY",
    "BINANCE_TESTNET_SECRET_KEY",
    "BITGET_API_KEY",
    "BITGET_SECRET_KEY",
    "BITGET_PASSPHRASE",
    "BITGET_PAPER_API_KEY",
    "BITGET_PAPER_SECRET_KEY",
    "BITGET_PAPER_PASSPHRASE",
    "KAIKO_API_KEY",
    "COINAPI_KEY",
]


def load_secrets(project_id: str = None, prefix: str = DEFAULT_PREFIX) -> bool:
    """
    Load secrets from GCP Secret Manager into os.environ.

    Returns True if Secret Manager was used, False if fell back to .env.
    """
    if project_id is None:
        project_id = os.environ.get("GCP_PROJECT_ID")

    if not project_id:
        logger.info("No GCP_PROJECT_ID set, falling back to .env")
        return _load_dotenv_fallback()

    try:
        from google.cloud import secretmanager
        client = secretmanager.SecretManagerServiceClient()
    except Exception as e:
        logger.warning("Secret Manager unavailable (%s), falling back to .env", e)
        return _load_dotenv_fallback()

    loaded = 0
    for var_name in EXPECTED_SECRETS:
        secret_id = f"{prefix}-{var_name}"
        resource = f"projects/{project_id}/secrets/{secret_id}/versions/latest"
        try:
            response = client.access_secret_version(request={"name": resource})
            value = response.payload.data.decode("utf-8").strip()
            os.environ[var_name] = value
            loaded += 1
        except Exception as e:
            logger.warning("Failed to load secret %s: %s", secret_id, e)

    logger.info("Loaded %d/%d secrets from Secret Manager (project=%s)",
                loaded, len(EXPECTED_SECRETS), project_id)
    return loaded > 0


def _load_dotenv_fallback() -> bool:
    try:
        from dotenv import load_dotenv
        dotenv_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            ".env"
        )
        if os.path.exists(dotenv_path):
            load_dotenv(dotenv_path)
            logger.info("Loaded credentials from %s", dotenv_path)
            return False
        else:
            logger.warning("No .env file found at %s", dotenv_path)
            return False
    except ImportError:
        logger.warning("python-dotenv not installed and no Secret Manager available")
        return False
