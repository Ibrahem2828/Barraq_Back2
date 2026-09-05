"""Test discovery limited to applications enabled by the Django project.

Django's default ``test`` command discovers every Python package below the
repository root. Restricting unlabeled runs to ``INSTALLED_APPS`` keeps a
deployment check from ever picking up a package that isn't installed or
routed by this project (e.g. an experimental app under development).

Explicit test labels remain untouched, so a developer can still run any
suite deliberately by name.
"""

from __future__ import annotations

from importlib.util import find_spec

from django.apps import apps
from django.test.runner import DiscoverRunner


class EnabledAppsDiscoverRunner(DiscoverRunner):
    """Discover tests from installed project apps when no label is supplied."""

    def build_suite(self, test_labels=None, **kwargs):
        if not test_labels:
            test_labels = [
                f"{app_config.name}.tests"
                for app_config in apps.get_app_configs()
                if app_config.name.startswith("apps.")
                and find_spec(f"{app_config.name}.tests") is not None
            ]
        return super().build_suite(test_labels, **kwargs)
