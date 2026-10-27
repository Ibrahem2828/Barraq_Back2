# OTP and password-reset email: reaching the inbox, not the spam folder

A verification code in the spam folder is a code that never arrived. This is what
was verified on 2026-10-27, what is controlled in code, what only DNS and a mail
provider can fix, and how to prove inbox placement before launch.

Mailbox providers decide placement from DNS authentication, the sending
provider's reputation and the message itself. No application change can replace
DNS ownership, sender verification or provider reputation: those are release
prerequisites owned by the domain and mail-provider administrator. This document
extends the earlier checklist (kept below as the release gate) with what is
actually configured in production today.

## What production does today (verified)

* Mail leaves through **Gmail SMTP** (`smtp.gmail.com:587`, STARTTLS) as
  `barraq.ai.team@gmail.com`, plain text only, no sender name.
* The domain **`baraqapp.com` has no email authentication at all**: no MX, no SPF
  record, no DKIM key, no DMARC policy (`python scripts/check_email_domain.py
  baraqapp.com`, exit status 1). Its name servers are Hostinger's default parking
  servers (`nebula/aurora.dns-parking.com`).
* The code's own default sender is `Baraq <no-reply@baraqapp.com>`; production
  overrides it with the Gmail address *because* the domain cannot authenticate mail.

What that means for a student:

| Situation | Today |
|---|---|
| Recipient is on Gmail | Almost always the inbox: Google is both sender and receiver. |
| Recipient is on Outlook, Yahoo, iCloud, a school or company server | Authenticated as `gmail.com` (Google's SPF/DKIM), so it passes, but reputation belongs to one personal account and these providers judge it more strictly. No guarantee. |
| Volume | A normal Gmail account allows about **500 messages a day** (Workspace about 2,000), shared by every OTP and reset email. Past that Gmail answers `550 5.4.5 Daily user sending limit exceeded`; the task retries three times and the student gets no code. |
| Reset links | The sender says gmail.com and the link says baraqapp.com: a mismatch filters read as phishing. |
| Account risk | A personal Google account sending automated mail can be rate-limited or locked, which silently stops every OTP. |

Gmail SMTP is acceptable for a small pilot. It is not a production sending path.

## What the code controls (done in this change)

`apps/users/emailing.py` builds both messages; `apps/users/test_emailing.py` pins every property:

* a display name on a valid From address (`برّاق <address>`), optional `Reply-To`
  (`EMAIL_REPLY_TO`), and a Message-ID in `EMAIL_MESSAGE_ID_DOMAIN`
  (default `baraqapp.com`) instead of the container's random hostname;
* `multipart/alternative`: a text part and an HTML part that say the same thing;
* an OTP message with **no link, image, script or tracking**; a reset message with
  **exactly one link whose visible text is its destination**;
* `Auto-Submitted: auto-generated` and `X-Auto-Response-Suppress: All`, so
  auto-responders and list software do not reply to it;
* short, calm wording in Arabic with the English equivalent below, the code in the
  subject (`رمز تحقق برّاق: 482913`) so it shows in the notification;
* only a 4-10 digit code is accepted; a link that is not http(s) is refused;
* nothing identifying is logged (no code, no address); the Message-ID is, so one
  send can be traced in the provider's logs;
* a backend that accepts zero messages is a failure and is retried (it used to
  look like success);
* unchanged from before: sends run on the `critical` Celery queue, SMTP failures
  retry three times with backoff, and `EMAIL_TIMEOUT` is bounded and validated.

## What only DNS and a provider can fix (not done: needs the owner)

1. **Use a transactional email provider, not a personal mailbox.** Any
   SMTP-compatible provider works with no code change, only environment:
   `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`,
   `DEFAULT_FROM_EMAIL="برّاق <no-reply@baraqapp.com>"`, optionally
   `EMAIL_REPLY_TO`. Amazon SES, Postmark, Mailgun, Brevo, Resend and SendGrid all
   qualify; for OTP specifically, a provider that separates transactional from
   marketing traffic (Postmark, SES) is the safe choice.
2. **Authenticate `baraqapp.com`.** Add these at the DNS host (Hostinger hPanel;
   confirm which zone is live first, the current name servers are parking ones):

   | Type | Name | Value |
   |---|---|---|
   | TXT | `@` | `v=spf1 include:<provider's SPF host> ~all` (exactly one SPF record) |
   | CNAME or TXT | `<selector>._domainkey` | exactly what the provider shows (DKIM public key) |
   | TXT | `_dmarc` | `v=DMARC1; p=none; rua=mailto:<report address>; adkim=s; aspf=s` |
   | MX | `@` | only if the domain should receive mail (replies, bounces, `support@`); otherwise set `EMAIL_REPLY_TO` to a real mailbox |

   Start DMARC at `p=none`, read the reports for two to four weeks, then move to
   `quarantine` and finally `reject`. The `rua` address must be able to receive
   mail: with no MX on the domain use a report address elsewhere or a DMARC
   report service.
3. **One verified domain end to end.** The visible From address and the SMTP
   envelope sender (Return-Path) must use the domain you authenticated. A
   production OTP must never be sent from a personal mailbox address, which is
   exactly what production does today. The provider supplies a valid reverse
   DNS/PTR and TLS certificate for its sending hosts; confirm both.
4. **Gate on it:** `python scripts/check_email_domain.py baraqapp.com
   --dkim-selector <selector>` must exit 0 before a release that sends mail.

## Proving inbox placement (required before launch)

DNS correct is necessary, not sufficient. Reputation and content are judged per
mailbox provider, so test real inboxes:

1. Send a real registration OTP to **seed inboxes** on Gmail, Outlook.com or
   Hotmail, Yahoo, iCloud and one company or school mailbox.
2. Each must arrive in the **Inbox** (Gmail: Primary tab) within about a minute.
3. In Gmail open *Show original*: `SPF: PASS`, `DKIM: PASS`, `DMARC: PASS`, and
   the domain in each (`header.d` for DKIM) must be `baraqapp.com`. Use only
   consented, seeded test addresses and never put real OTP values in tickets or logs.
4. Send one message to a spam-test address (for example mail-tester.com); require
   a score of 9/10 or better with SPF, DKIM and DMARC all green.
5. Verify the domain in Google Postmaster Tools and watch the domain reputation
   and spam-rate graphs after launch.

Repeat after any change of provider, sender address, domain, or message wording.

## Operating it

* Alert when OTP sends fail repeatedly (the worker logs `otp_email_handed_to_smtp`
  on success; Celery retries three times with backoff) and when a daily-limit or
  authentication error appears.
* Take bounce and complaint webhooks from the provider into a suppression list;
  keep complaint rate under 0.1%.
* Per-student resend is already limited (5 an hour, 60-second cooldown) so one
  account cannot burn the sending reputation.
