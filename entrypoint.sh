#!/bin/sh
set -eu

mkdir -p "${MEDIA_ROOT:-/app/media}" /app/staticfiles

if [ "${DJANGO_WAIT_FOR_DATABASE:-1}" = "1" ]; then
  python - <<'PY'
import os
import time
from django.db import connections
from django.db.utils import OperationalError

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
for attempt in range(30):
    try:
        connections["default"].cursor().execute("SELECT 1")
        break
    except OperationalError:
        if attempt == 29:
            raise
        time.sleep(2)
PY
fi

if [ "${DJANGO_RUN_MIGRATIONS:-0}" = "1" ]; then
  python manage.py migrate --noinput
fi

if [ "${DJANGO_COLLECTSTATIC:-0}" = "1" ]; then
  python manage.py collectstatic --noinput
fi

exec "$@"
