# Subscriptions Core

Phase Admin-2 adds an internal subscription system. It does not integrate Stripe, PayPal, or any external payment provider.

## Models

- `SubscriptionPlan`: Free, Premium, Pro, School plan definitions with `features` and `limits`.
- `UserSubscription`: one current subscription per user.
- `SubscriptionUsage`: monthly usage counters.
- `SubscriptionEvent`: local subscription lifecycle events.

Every user should have a Free subscription. Services still fall back to Free if a subscription row is missing.

## Default Plans

- `free`: 3 collections, 20 sources, 10 MB per file, 200 MB storage, Khota/Fahes/Rasheed enabled, Kholasa/Sada disabled.
- `premium`: 50 collections, 500 sources, 50 MB per file, 5000 MB storage, all characters enabled.
- `pro`: higher individual limits for intensive use, all characters enabled.
- `school`: institutional custom plan with very high limits, hidden from public plan listing by default.

## Enforcement

All checks go through `apps.subscriptions.services`.

- Creating a student source collection checks `can_create_collection`.
- Uploading a student source checks `can_upload_source`.
- Using source or collection characters checks `can_use_character`.
- Successful creates/uploads/character calls consume monthly usage.

Admin APIs are not blocked by student plan limits.

## Student APIs

- `GET /api/subscriptions/me/`: current plan, subscription, usage, limits, features, and remaining limits.
- `GET /api/subscriptions/plans/`: active public plans.

Both require normal JWT authentication and only expose the current user context.

## Admin APIs

- `GET|POST /api/admin/subscription-plans/`
- `GET|PATCH|DELETE /api/admin/subscription-plans/{id}/`
- `GET /api/admin/user-subscriptions/`
- `GET|PATCH /api/admin/user-subscriptions/{id}/`
- `POST /api/admin/users/{id}/change-subscription/`
- `POST /api/admin/users/{id}/cancel-subscription/`
- `GET /api/admin/subscription-usage/`

These endpoints require the existing Admin RBAC permissions:

- `subscription_plans.view`
- `subscription_plans.create`
- `subscription_plans.update`
- `subscription_plans.delete`
- `subscriptions.view`
- `subscriptions.update`
- `subscriptions.cancel`

Admin subscription changes write `AuditLog` rows.

## Limit Errors

Limit errors return a DRF error response, not `500`.

Examples:

```json
{
  "detail": "لقد وصلت إلى الحد الأقصى للمصادر في خطتك الحالية.",
  "code": "source_limit_reached",
  "limit": 20,
  "usage": 20
}
```

```json
{
  "detail": "هذه الشخصية غير متاحة في خطتك الحالية.",
  "code": "character_not_allowed",
  "character": "kholasa"
}
```

## Future Payment Integration

The model already has provider fields such as `provider`, `provider_subscription_id`, and metadata. A future Stripe integration should map webhook events into `UserSubscription` and `SubscriptionEvent` without changing student-facing source or character APIs.
