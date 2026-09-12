# Build from the repository root: docker build -f backend/Dockerfile .
FROM python:3.10-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONNOUSERSITE=1 PYTHONPATH=/app \
    SIMULATION_MANAGER_MODE=redis CITYPULSE_ENV_FILE="" CITYPULSE_SECRETS_DIR=/run/secrets
COPY backend/requirements.txt /tmp/backend-requirements.txt
COPY deploy/requirements/runtime-constraints.txt /tmp/runtime-constraints.txt
RUN pip install --no-cache-dir --timeout 120 torch==2.13.0+cpu --index-url https://download.pytorch.org/whl/cpu --extra-index-url https://pypi.org/simple
RUN pip install --no-cache-dir --timeout 120 -c /tmp/runtime-constraints.txt -r /tmp/backend-requirements.txt
COPY backend /app/backend
COPY scenario_catalog /app/scenario_catalog
COPY simulation_protocol /app/simulation_protocol
COPY traffic_eval /app/traffic_eval
COPY traffic_intelligence /app/traffic_intelligence
COPY traffic_llm_runtime /app/traffic_llm_runtime
# Backend only needs the static registry, never algorithm implementations/checkpoints.
COPY traffic_control/__init__.py traffic_control/registry.py /app/traffic_control/
COPY traffic_knowledge /app/traffic_knowledge
COPY scripts/deployment/check_map_artifacts.py /app/scripts/deployment/check_map_artifacts.py
RUN mkdir -p /run/secrets /app/outputs \
    && python -c "import importlib.util; assert all(importlib.util.find_spec(m) is None for m in ('simulation', 'sumolib', 'libsumo', 'traci'))"
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
