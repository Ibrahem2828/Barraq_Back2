# Registration and email OTP flow

## Security boundary

`POST /api/v1/auth/register/` never creates a permanent `users.User`.
After validation, it writes only one `users.PendingRegistration` row using a
canonical lower-case email identity. The row contains the submitted profile
fields, a Django password hash, a Django OTP hash, expiry, attempt counter and
bounded send-window metadata. It never contains a raw password or OTP.

```
Browser -> Next.js BFF -> Django register
                         -> PendingRegistration (committed)
                         -> Celery critical -> SMTP

Browser -> Next.js BFF -> Django verify-email
                         -> lock PendingRegistration
                         -> validate current OTP
                         -> User + StudentProfile + pending deletion (one transaction)
                         -> normal access/refresh tokens
```

The only compatibility exception is a narrowly scoped path for historic
`User(is_verified=False)` rows that predate this design. New registrations do
not enter that path. It exists so a migration does not destructively strand a
learner who was already partway through the old flow.

## Endpoints

### `POST /auth/register/`

Validates and normalizes the form, rejects an existing permanent account,
creates or safely continues a pending registration, and queues one OTP when a
new code is due. The successful response is:

```json
{
  "verification_required": true,
  "email": "normalized@example.com",
  "expires_in": 600,
  "resend_after_seconds": 60
}
```

A duplicate post while the existing OTP is still in cooldown preserves the
already-pending password/profile data and returns the same verification state;
it does not overwrite credentials. After expiry, a validated new form starts a
fresh pending attempt. Existing-account disclosure is intentionally preserved
for the established registration UX and protected by the registration throttle.

### `POST /auth/resend-otp/`

Uses a row lock, server-side 60-second cooldown by default, and a per-email
send ceiling of five codes per 60-minute window by default. A new OTP replaces
the stored hash and resets attempts by intentional policy. Resend remains
silent for unknown or already verified addresses.

### `POST /auth/verify-email/`

Locks the pending row with `select_for_update()` inside `transaction.atomic()`.
It checks expiry and attempt limit, compares the Django OTP hash, creates the
permanent user with the existing password hash (not a double hash), creates the
student profile and deletes the pending record in one transaction. Incorrect
attempt increments are committed before the API error is returned. No pending
record, replay, or an already verified email can issue session tokens.

## Error and failure behavior

Stable top-level domain codes include `email_already_registered`, `otp_invalid`,
`otp_expired`, `otp_too_many_attempts`, `otp_resend_cooldown`,
`otp_resend_limit_exceeded`, `pending_registration_missing`,
`email_delivery_unavailable`, and `registration_conflict`.

If enqueueing to Celery fails synchronously, Django returns the retryable
`email_delivery_unavailable` error. The pending record remains intact; the
client can retry after the normal cooldown. If SMTP fails asynchronously, the
critical task uses bounded Celery retries with the same code; no new account is
created and no raw OTP is logged.

## Retention and operations

`users.cleanup_expired_pending_registrations` deletes only pending rows whose
`updated_at` exceeds `PENDING_REGISTRATION_RETENTION_HOURS` (24 hours by
default). Celery Beat schedules it hourly. The task uses the normal queue;
OTP/password-reset email stays routed to `critical`, consumed by
`backend-worker-critical` with backend egress.

Real SMTP receipt and PostgreSQL row-locking are release-candidate gates. The
test suite proves the SMTP handoff boundary and contains a PostgreSQL-only
two-request concurrency test, which must run on the candidate database.
