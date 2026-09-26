web: uvicorn pr_sentinel.ingress.app:app --host 0.0.0.0 --port ${PORT:-8000}
worker: python -m pr_sentinel.cli worker
dashboard: uvicorn pr_sentinel.dashboard.app:app --host 0.0.0.0 --port ${DASHBOARD_PORT:-8001}
