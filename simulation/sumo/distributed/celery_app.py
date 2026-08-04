"""Celery application for isolated SUMO worker processes."""

from __future__ import annotations

import os

from celery import Celery

app = Celery(
    "citypulse_sumo",
    broker=os.getenv(
        "CITYPULSE_CELERY_BROKER_URL", "redis://127.0.0.1:6379/0"
    ),
    backend=os.getenv(
        "CITYPULSE_CELERY_RESULT_BACKEND", "redis://127.0.0.1:6379/2"
    ),
    include=("simulation.sumo.distributed.tasks",),
)
app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=("json",),
    task_acks_late=False,
    task_reject_on_worker_lost=False,
    worker_prefetch_multiplier=1,
    result_expires=int(os.getenv("CITYPULSE_SESSION_TTL_SECONDS", "86400")),
    task_default_queue="citypulse-sumo",
    task_track_started=True,
    broker_connection_retry_on_startup=True,
)
