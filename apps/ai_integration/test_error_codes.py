"""The failure vocabulary shared with the AI service.

The AI service names why a job failed; Django must keep that meaning, say it in
fixed safe text, and know whether trying again can help.
"""

from django.test import SimpleTestCase

from .error_codes import (
    ErrorCode,
    is_retryable_error,
    map_upstream_error_code,
    public_error_message,
)


class BudgetExhaustionTests(SimpleTestCase):
    def test_an_exhausted_provider_budget_keeps_its_own_code(self):
        self.assertEqual(
            map_upstream_error_code(status_code=502, remote_code='provider_budget_exhausted'),
            ErrorCode.PROVIDER_BUDGET_EXHAUSTED,
        )

    def test_retrying_cannot_fix_an_exhausted_budget(self):
        self.assertFalse(is_retryable_error(ErrorCode.PROVIDER_BUDGET_EXHAUSTED))

    def test_it_has_its_own_fixed_public_message(self):
        message = public_error_message(ErrorCode.PROVIDER_BUDGET_EXHAUSTED)
        self.assertIn('usage limit', message)
        self.assertNotEqual(message, public_error_message(ErrorCode.ALL_PROVIDERS_FAILED))


class UnknownUpstreamCodeTests(SimpleTestCase):
    def test_a_rate_limited_response_with_an_unknown_code_is_still_rate_limited(self):
        self.assertEqual(
            map_upstream_error_code(status_code=429, remote_code='too_many_active_jobs'),
            ErrorCode.PROVIDER_RATE_LIMITED,
        )

    def test_a_refusal_with_an_unknown_code_is_a_contract_problem_not_invented_meaning(self):
        self.assertEqual(
            map_upstream_error_code(status_code=403, remote_code='character_not_enabled'),
            ErrorCode.INVALID_CONTRACT,
        )

    def test_every_catalogued_code_has_a_message_or_the_generic_one(self):
        for code in ErrorCode.values_set():
            self.assertTrue(public_error_message(code))
