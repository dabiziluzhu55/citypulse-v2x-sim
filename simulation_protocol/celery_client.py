"""Minimal Celery client for Backend task dispatch (no worker task imports)."""

from __future__ import annotations

import os

from celery import Celery

_client: Celery | None = None


def get_celery_client() -> Celery:
    global _client
    if _client is None:
        _client = Celery(
            "citypulse_sumo_client",
            broker=os.getenv(
                "CITYPULSE_CELERY_BROKER_URL", "redis://127.0.0.1:6379/0"
            ),
            backend=os.getenv(
                "CITYPULSE_CELERY_RESULT_BACKEND", "redis://127.0.0.1:6379/2"
            ),
        )
        _client.conf.update(
            task_serializer="json",
            result_serializer="json",
            accept_content=("json",),
            task_default_queue="citypulse-sumo",
            broker_connection_retry_on_startup=True,
        )
    return _client
