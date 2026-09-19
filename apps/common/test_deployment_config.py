"""Guards on the production deployment configuration.

`docker compose config --quiet` is the authoritative validator and is an RC
gate. These tests cover what can be asserted without a Docker CLI, and in
particular the properties whose loss is silent: a published database port, a
worker that can no longer reach SMTP, a public route to the private AI
service, or an upload size chain where one hop refuses what the hop in front
of it promised.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

import yaml
from django.conf import settings
from django.test import SimpleTestCase

DEPLOY_DIR = Path(settings.BASE_DIR) / 'deploy' / 'production'
COMPOSE_PATH = DEPLOY_DIR / 'compose.yaml'
CADDYFILE_PATH = DEPLOY_DIR / 'Caddyfile'

#: Services that must never publish a port to the host.
MUST_NOT_PUBLISH = {
    'backend-db',
    'ai-db',
    'redis',
    'backend-worker',
    'backend-worker-critical',
    'backend-beat',
    'ai-api',
    'ai-worker',
    'ai-beat',
}


def _is_credentialless_url(value):
    """True for a plain http(s) URL carrying no `user:pass@` userinfo part."""

    if not value.startswith(('http://', 'https://')):
        return False
    try:
        parts = urlsplit(value)
    except ValueError:
        return False
    return parts.username is None and parts.password is None


def load_compose():
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding='utf-8'))


class ProductionComposeTests(SimpleTestCase):
    def setUp(self):
        self.compose = load_compose()
        self.services = self.compose['services']

    def test_the_production_compose_file_is_version_controlled(self):
        """It lived as an untracked file on one machine, which meant the
        deployment could not be reproduced from any repository."""
        self.assertTrue(COMPOSE_PATH.is_file())
        self.assertTrue(CADDYFILE_PATH.is_file())

    def test_it_parses_and_declares_the_expected_topology(self):
        self.assertEqual(self.compose['name'], 'baraq-production')
        self.assertEqual(
            set(self.compose['networks']),
            {'edge', 'private', 'backend-egress', 'provider-egress'},
        )

    def test_the_private_network_is_internal(self):
        """`internal: true` is what keeps databases and workers off the
        public bridge. Losing it is invisible until someone scans the host."""
        self.assertIs(self.compose['networks']['private']['internal'], True)

    def test_only_the_gateway_publishes_ports_and_only_on_loopback(self):
        for name, service in self.services.items():
            ports = service.get('ports') or []
            with self.subTest(service=name):
                if name != 'gateway':
                    self.assertEqual(ports, [], f'{name} must not publish a port')
                    continue
                for mapping in ports:
                    self.assertTrue(
                        str(mapping).startswith('127.0.0.1:'),
                        f'gateway port {mapping} is not bound to loopback',
                    )

    def test_no_datastore_or_worker_publishes_a_port(self):
        for name in MUST_NOT_PUBLISH:
            with self.subTest(service=name):
                self.assertIn(name, self.services)
                self.assertFalse(self.services[name].get('ports'))

    def test_the_private_ai_service_is_not_reachable_from_the_edge(self):
        """The browser reaches AI through Next.js -> Django -> private AI.
        Putting ai-api on `edge` would expose the internal API directly."""
        self.assertEqual(self.services['ai-api']['networks'], ['private'])
        self.assertNotIn('ai-api', CADDYFILE_PATH.read_text(encoding='utf-8'))

    def test_the_critical_worker_can_still_reach_smtp(self):
        """`private` is internal, so it has no route out. Without
        `backend-egress` the OTP worker resolves nothing, every send fails at
        DNS, and registration silently stops working."""
        networks = self.services['backend-worker-critical']['networks']
        self.assertIn('private', networks)
        self.assertIn(
            'backend-egress',
            networks,
            'the critical worker lost its SMTP egress; registration OTP will fail',
        )
        self.assertIs(
            self.compose['networks']['backend-egress'].get('internal', False),
            False,
        )

    def test_the_ai_worker_keeps_its_provider_egress(self):
        self.assertIn('provider-egress', self.services['ai-worker']['networks'])

    def test_no_secret_value_is_committed(self):
        """Every credential must be a ${VAR} reference resolved at deploy
        time. A value here would be a secret in Git history forever."""
        raw = COMPOSE_PATH.read_text(encoding='utf-8')
        for line in raw.splitlines():
            stripped = line.strip()
            if stripped.startswith('#') or ':' not in stripped:
                continue
            key, _, value = stripped.partition(':')
            if not any(
                marker in key.upper()
                for marker in ('PASSWORD', 'SECRET', 'API_KEY', 'TOKEN', 'DSN')
            ):
                continue
            value = value.strip().strip('"\'')
            if not value:
                continue
            if _is_credentialless_url(value):
                # e.g. FRONTEND_PASSWORD_RESET_URL -- the name matches on
                # "PASSWORD" but the value is a public link carrying no
                # userinfo. A connection string with an embedded credential
                # does contain `@` and is still checked.
                continue
            with self.subTest(line=stripped):
                self.assertIn('${', value, f'{key.strip()} looks like a literal secret')


class UploadSizeChainTests(SimpleTestCase):
    """Every hop must clear the one behind it.

    A lower limit upstream refuses a file the user was told was acceptable,
    at a layer they cannot see.
    """

    def setUp(self):
        self.compose = load_compose()
        self.caddyfile = CADDYFILE_PATH.read_text(encoding='utf-8')

    def _bff_limit_bytes(self):
        web = self.compose['services']['web']['environment']
        return int(web['BFF_MAX_BODY_BYTES'])

    def _caddy_limit_bytes(self):
        import re

        sizes = {int(value) for value in re.findall(r'max_size\s+(\d+)MB', self.caddyfile)}
        self.assertTrue(sizes, 'Caddyfile declares no request_body max_size')
        return min(sizes) * 1024 * 1024

    def test_the_bff_accepts_at_least_what_django_does(self):
        django_limit = settings.STUDENT_SOURCE_MAX_UPLOAD_MB * 1024 * 1024
        self.assertGreaterEqual(
            self._bff_limit_bytes(),
            django_limit,
            'the BFF would 413 a file the backend accepts',
        )

    def test_caddy_accepts_at_least_what_the_bff_forwards(self):
        self.assertGreaterEqual(
            self._caddy_limit_bytes(),
            self._bff_limit_bytes(),
            'Caddy would refuse a body the BFF is willing to forward',
        )

    def test_the_headroom_is_deliberate_but_not_unbounded(self):
        """Multipart overhead needs room; an order of magnitude would mean
        the cap had stopped meaning anything."""
        django_limit = settings.STUDENT_SOURCE_MAX_UPLOAD_MB * 1024 * 1024
        self.assertLess(self._caddy_limit_bytes(), django_limit * 4)
