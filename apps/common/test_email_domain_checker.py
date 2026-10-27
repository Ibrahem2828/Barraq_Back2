"""The release gate that reads a sending domain's SPF/DMARC/DKIM must itself be right.

A checker that calls an authenticated domain broken is ignored; one that calls a
broken domain fine ships spam-folder OTPs. DNS is faked: no network in tests.
"""

import importlib.util
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts' / 'check_email_domain.py'
spec = importlib.util.spec_from_file_location('check_email_domain', SCRIPT)
assert spec is not None and spec.loader is not None
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def with_records(records):
    return mock.patch.object(checker, 'query', side_effect=lambda name, kind: records.get((name, kind), []))


class SpfTests(SimpleTestCase):
    def verdict(self, record):
        with with_records({('example.com', 'TXT'): [record] if record else []}):
            return checker.check_spf('example.com')[0]

    def test_a_provider_include_with_a_soft_or_hard_fail_is_fine(self):
        self.assertTrue(self.verdict('v=spf1 include:amazonses.com ~all'))
        self.assertTrue(self.verdict('v=spf1 include:_spf.example.net -all'))

    def test_a_redirect_is_fine(self):
        self.assertTrue(self.verdict('v=spf1 redirect=_spf.google.com'))

    def test_missing_open_or_unterminated_spf_fails(self):
        self.assertFalse(self.verdict(''))
        self.assertFalse(self.verdict('v=spf1 +all'))
        self.assertFalse(self.verdict('v=spf1 ip4:203.0.113.7'))

    def test_two_spf_records_are_invalid(self):
        with with_records({('example.com', 'TXT'): ['v=spf1 -all', 'v=spf1 include:a.example ~all']}):
            self.assertFalse(checker.check_spf('example.com')[0])

    def test_more_than_ten_lookups_is_invalid(self):
        record = 'v=spf1 ' + ' '.join(f'include:s{i}.example' for i in range(11)) + ' ~all'
        self.assertFalse(self.verdict(record))


class DmarcTests(SimpleTestCase):
    def verdict(self, record):
        with with_records({('_dmarc.example.com', 'TXT'): [record] if record else []}):
            return checker.check_dmarc('example.com')

    def test_any_policy_with_reports_passes(self):
        for policy in ('none', 'quarantine', 'reject'):
            self.assertTrue(self.verdict(f'v=DMARC1; p={policy}; rua=mailto:r@example.com')[0], policy)

    def test_monitoring_only_is_called_out(self):
        self.assertIn('monitoring only', self.verdict('v=DMARC1; p=none; rua=mailto:r@example.com')[1])

    def test_missing_or_policyless_dmarc_fails(self):
        self.assertFalse(self.verdict('')[0])
        self.assertFalse(self.verdict('v=DMARC1; rua=mailto:r@example.com')[0])


class DkimTests(SimpleTestCase):
    def test_a_published_key_passes_and_a_revoked_or_missing_one_fails(self):
        records = {
            ('good._domainkey.example.com', 'TXT'): ['v=DKIM1; k=rsa; p=MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8A'],
            ('revoked._domainkey.example.com', 'TXT'): ['v=DKIM1; k=rsa; p='],
        }
        with with_records(records):
            result = {s: ok for s, ok, _ in checker.check_dkim('example.com', ['good', 'revoked', 'absent'])}

        self.assertEqual(result, {'good': True, 'revoked': False, 'absent': False})


class TxtParsingTests(SimpleTestCase):
    def test_unquoted_and_quoted_txt_data_are_both_read(self):
        class Response:
            def __init__(self, data):
                self.data = data

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, *args):
                return self.data

        import json

        for data in ('v=spf1 -all', '"v=spf1 " "-all"'):
            payload = json.dumps({'Answer': [{'type': 16, 'data': data}]}).encode()
            with mock.patch.object(checker.urllib.request, 'urlopen', return_value=Response(payload)):
                self.assertEqual(checker.query('example.com', 'TXT'), ['v=spf1 -all'])
