try:
    from .celery import app as celery_app
except ImportError:  # Allows management commands before optional worker packages are installed.
    celery_app = None

__all__ = ('celery_app',)
