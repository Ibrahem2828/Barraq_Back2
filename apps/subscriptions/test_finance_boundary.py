"""What Baraq may and may not claim about money.

Two invariants, both worth pinning before a release that mentions billing.

First: no model may hold cardholder data. Baraq has no payment integration
at all today, so this passes by absence -- which is the safest way to pass
it. The test exists so that adding one later is a deliberate decision made
against a failing test, rather than a field quietly appearing on a model
during a busy week.

Second: the database records subscriptions, not payments. There is no
transaction, invoice, charge or refund anywhere, so the only money-shaped
number in the system is a plan's catalogue price. Multiplying that by a
subscriber count produces a figure that looks like revenue and is not one:
it counts free trials, cancelled-but-unexpired subscriptions and manually
granted plans at list price, and it cannot see a single failed payment.
The test refuses that shortcut by name.
"""

from __future__ import annotations

from decimal import Decimal

from django.apps import apps
from django.test import TestCase

from .models import SubscriptionPlan, UserSubscription

#: Substrings that suggest a field is holding, or about to hold, data that
#: brings Baraq inside a PCI scope it is not built for.
CARDHOLDER_HINTS = (
    "card_number",
    "cardnumber",
    "cardholder",
    "cvv",
    "cvc",
    "pan",
    "iban",
    "routing_number",
    "account_number",
    "exp_month",
    "exp_year",
    "expiry",
)

#: Model names that would mean money movement is being recorded. If one of
#: these appears, the finance reporting rules below stop being hypothetical.
MONEY_MOVEMENT_HINTS = ("payment", "transaction", "invoice", "charge", "refund")


class CardholderDataBoundaryTests(TestCase):
    def test_no_model_can_store_cardholder_data(self):
        """Provider-hosted payment or nothing.

        Storing a card number, a CVV or a bank account puts Baraq inside a
        compliance scope its architecture makes no claim to. Tokenised,
        provider-hosted payment keeps that data somewhere it belongs.
        """
        offenders = [
            f"{model._meta.label}.{field.name}"
            for model in apps.get_models()
            for field in model._meta.get_fields()
            if any(hint in getattr(field, "name", "").lower() for hint in CARDHOLDER_HINTS)
        ]

        self.assertEqual(
            offenders,
            [],
            "a model gained a field that looks like cardholder data; Baraq is not PCI-scoped",
        )

    def test_the_system_records_subscriptions_not_payments(self):
        """A statement of what the data actually supports.

        This is not a rule against building billing. It is a marker: the day
        a payment model appears, the reporting rules in this file have to be
        revisited, because "revenue" stops being unknowable and starts being
        a number with a right answer.
        """
        money_models = [
            model._meta.label
            for model in apps.get_models()
            if any(hint in model.__name__.lower() for hint in MONEY_MOVEMENT_HINTS)
        ]

        self.assertEqual(
            money_models,
            [],
            "money movement is now recorded: revisit finance reporting, "
            "webhook idempotency and the PCI boundary before release",
        )


class SubscriptionReportingHonestyTests(TestCase):
    """Guards against the one metric that is tempting and wrong."""

    def setUp(self):
        self.free = SubscriptionPlan.objects.create(
            code="honesty-free", name="Free", price=Decimal("0.00"), currency="USD"
        )
        self.paid = SubscriptionPlan.objects.create(
            code="honesty-paid", name="Paid", price=Decimal("10.00"), currency="USD"
        )
        self.other_currency = SubscriptionPlan.objects.create(
            code="honesty-sar", name="Paid SAR", price=Decimal("40.00"), currency="SAR"
        )

    def test_a_plan_price_is_a_catalogue_price_not_an_amount_paid(self):
        """Nothing links a subscription to money that changed hands.

        UserSubscription records which plan an account is on and how it got
        there -- manual, promo, local. None of those mean a payment was
        captured, so a subscriber count cannot be priced.
        """
        subscription_fields = {field.name for field in UserSubscription._meta.get_fields()}

        for field in ("amount_paid", "paid_at", "currency", "transaction_id", "invoice_id"):
            self.assertNotIn(
                field,
                subscription_fields,
                f"UserSubscription.{field} exists: subscriptions may now carry payment "
                "facts, so reporting can and should use them instead of plan price",
            )

    def test_plans_may_differ_in_currency_so_totals_cannot_be_summed(self):
        """Two currencies, no exchange rate anywhere in the system.

        Summing them yields a number with no unit. Any reporting has to
        group by currency until a real FX layer exists.
        """
        currencies = set(
            SubscriptionPlan.objects.filter(code__startswith="honesty-").values_list(
                "currency", flat=True
            )
        )

        self.assertGreater(len(currencies), 1)
        self.assertFalse(
            any(
                "exchange" in model.__name__.lower() or "fxrate" in model.__name__.lower()
                for model in apps.get_models()
            ),
            "an FX layer exists: cross-currency totals may now be computed deliberately",
        )
