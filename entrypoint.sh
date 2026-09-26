#!/bin/sh
set -e

case "${SERVICE_TYPE:-webhook}" in
  webhook)
    exec uvicorn pr_sentinel.ingress.app:app --host 0.0.0.0 --port "${PORT:-8000}"
    ;;
  worker)
    exec python -m pr_sentinel.cli worker
    ;;
  dashboard)
    exec uvicorn pr_sentinel.dashboard.app:app --host 0.0.0.0 --port "${PORT:-8001}"
    ;;
  *)
    echo "Unknown SERVICE_TYPE: ${SERVICE_TYPE}" >&2
    exit 1
    ;;
esac
