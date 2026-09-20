"""The request-body limit has to be enforced where the application reads it.

Baraq caps request bodies at four places: Caddy, the Next BFF, Django's
DATA_UPLOAD_MAX_MEMORY_SIZE, and the per-plan check in the sources app. The
numbers descend inward, so the smallest limit is the one the user was told
about and no layer stops them at a lower, invisible one.

That reasoning assumed Django's limit actually applies. For JSON and
form-urlencoded bodies read through DRF's ``request.data`` it did not:
djangorestframework 3.16.1 parsed the body itself and never consulted the
setting (CVE-2026-73228). Every Baraq API endpoint reads ``request.data``,
so the third layer of the chain was absent for exactly the content type the
API actually speaks. Measured on the pinned version, a 5 KB JSON body parsed
cleanly against a 1 KB limit.

This test fails on any release that reintroduces that gap -- whether by
pinning back to an affected version or by a future regression -- which is
the only way the assumption stays true rather than merely stated.
"""

from __future__ import annotations

import json
from urllib.parse import urlencode

from django.core.exceptions import RequestDataTooBig
from django.test import RequestFactory, TestCase, override_settings
from rest_framework.parsers import FormParser, JSONParser
from rest_framework.request import Request

#: Comfortably above the limit set below, and far below any real limit, so
#: the test is about enforcement rather than about a particular threshold.
LIMIT_BYTES = 1_000
OVERSIZED_PAYLOAD = "x" * 5_000


class RequestDataRespectsUploadLimitTests(TestCase):
    """``request.data`` must honour DATA_UPLOAD_MAX_MEMORY_SIZE."""

    def setUp(self):
        self.factory = RequestFactory()

    @override_settings(DATA_UPLOAD_MAX_MEMORY_SIZE=LIMIT_BYTES)
    def test_oversized_json_body_is_refused(self):
        body = json.dumps({"payload": OVERSIZED_PAYLOAD}).encode()
        self.assertGreater(len(body), LIMIT_BYTES)

        request = Request(
            self.factory.post("/", data=body, content_type="application/json"),
            parsers=[JSONParser()],
        )

        with self.assertRaises(RequestDataTooBig):
            _ = request.data

    @override_settings(DATA_UPLOAD_MAX_MEMORY_SIZE=LIMIT_BYTES)
    def test_oversized_form_body_is_refused(self):
        # Explicitly urlencoded: RequestFactory defaults a dict body to
        # multipart, which FormParser does not claim.
        request = Request(
            self.factory.post(
                "/",
                data=urlencode({"payload": OVERSIZED_PAYLOAD}),
                content_type="application/x-www-form-urlencoded",
            ),
            parsers=[FormParser()],
        )

        with self.assertRaises(RequestDataTooBig):
            _ = request.data

    @override_settings(DATA_UPLOAD_MAX_MEMORY_SIZE=LIMIT_BYTES)
    def test_a_body_within_the_limit_still_parses(self):
        """The limit must reject oversized bodies, not all of them."""
        request = Request(
            self.factory.post(
                "/", data=json.dumps({"payload": "small"}), content_type="application/json"
            ),
            parsers=[JSONParser()],
        )

        self.assertEqual(request.data, {"payload": "small"})
