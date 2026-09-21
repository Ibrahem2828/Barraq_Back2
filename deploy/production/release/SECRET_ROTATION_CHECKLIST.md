# Secret rotation checklist

## Immediate release requirements

- [ ] Rotate all production credentials through the approved secret manager
  before candidate promotion if they were ever copied into a workstation,
  untracked file, historic commit, ticket, log, or chat.
- [ ] Rotate Django secret/JWT signing material using the platform's supported
  overlap strategy; invalidate/revoke old sessions as required.
- [ ] Rotate Backend↔AI HMAC key material with current/previous key IDs during
  a bounded overlap window; verify replay protection and remove retired keys.
- [ ] Rotate database, Redis, SMTP, object-storage, provider, Sentry, and any
  third-party integration credentials according to their service procedures.
- [ ] Regenerate recovery codes in the identity provider/password manager and
  invalidate the prior set after secure storage is confirmed.

## Audit findings

- Current tracked release trees had no high-confidence API/private-key literal
  hits in the filename-only source scan.
- Historical Backend commits contained demo credential strings. They have been
  removed from the current tree and the seed command now requires runtime-only
  inputs, but any environment that ever used them must rotate those accounts.
- A recovery-code artifact exists at
  `D:\baraaq\Backup-codes-barraq.ai.team.txt`. It is outside every release
  repository. Its contents were not read during validation. Store it in the
  approved secure system or regenerate it, then remove the old local copy under
  the owner's controlled procedure. It must never be included in upload,
  archive, image, log, or support bundle.

## Verification without disclosure

- [ ] Confirm secret-manager version/audit events, not secret values.
- [ ] Verify each dependent service starts with the new value.
- [ ] Revoke old provider/API tokens and confirm they no longer authenticate.
- [ ] Re-run controlled SMTP, HMAC, storage, and provider smoke tests.
- [ ] Update ownership, rotation date, expiry/next rotation, and incident
  reference in the private secret inventory.

