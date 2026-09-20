"""Test discovery limited to applications enabled by the Django project.

Django's default ``test`` command discovers every Python package below the
repository root. Restricting unlabeled runs to ``INSTALLED_APPS`` keeps a
deployment check from ever picking up a package that isn't installed or
routed by this project (e.g. an experimental app under development).

Explicit test labels remain untouched, so a developer can still run any
suite deliberately by name.

Discovery walks each installed app package with Django's usual ``test*.py``
pattern. It previously named ``<app>.tests`` directly, which silently
excluded every other test module in the project.
"""

from __future__ import annotations

from importlib.util import find_spec

from django.apps import apps
from django.test.runner import DiscoverRunner


class EnabledAppsDiscoverRunner(DiscoverRunner):
    """Discover tests from installed project apps when no label is supplied."""

    def build_suite(self, test_labels=None, **kwargs):
        if not test_labels:
            # The app package, not `<app>.tests`. Naming the module meant
            # discovery stopped at exactly one file per app, so any other
            # test module -- test_concurrency.py, test_security.py -- was
            # never run by an unlabeled suite and never appeared in CI. It
            # failed silently, which is the worst way for a test to fail.
            # Handing DiscoverRunner the package lets its normal `test*.py`
            # pattern apply, while the INSTALLED_APPS filter keeps the
            # original intent.
            test_labels = [
                app_config.name
                for app_config in apps.get_app_configs()
                if app_config.name.startswith("apps.")
                and find_spec(app_config.name) is not None
            ]
        return super().build_suite(test_labels, **kwargs)
