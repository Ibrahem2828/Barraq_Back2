# Transactional email deliverability

The application hands OTP and password-reset mail to SMTP asynchronously on
the critical Celery queue and retries SMTP failures.  That guarantees neither
inbox placement nor provider acceptance: mailbox providers make that decision
from DNS authentication and the sending provider's reputation.

Before production release, the domain owner must complete and verify all of
the following for the exact domain used by `DEFAULT_FROM_EMAIL`:

1. Configure a transactional SMTP provider with a verified sender domain.
2. Publish the provider's SPF record.  There must be one merged SPF TXT
   record for the domain, not multiple competing records.
3. Publish and enable the provider's DKIM selector(s), then confirm a real
   received message has `dkim=pass` and aligned `header.d`.
4. Publish a DMARC record, beginning with monitoring (`p=none`) and moving to
   `quarantine`/`reject` only after alignment reports are clean.
5. Ensure the SMTP provider has a valid reverse DNS/PTR record and a TLS
   certificate for its sending hostname.
6. Use the same verified domain in the visible From address and SMTP envelope
   sender.  Do not send production OTPs from a personal mailbox address.
7. Send seeded, consented test mail to Gmail and Outlook and inspect the
   authentication results.  Never log OTP values while doing so.

Code-side safeguards in this repository:

- SMTP sends run on the `critical` queue and retry transient failures.
- SMTP `EMAIL_TIMEOUT` is bounded and validated as positive.
- SMTP returning zero accepted messages is treated as a failure, not success.
- OTP/password-reset bodies are concise transactional text and do not include
  trackers, attachments, or promotional content.

No application change can replace DNS ownership, sender verification, or
provider reputation.  Those are release prerequisites owned by the domain and
mail-provider administrator.
