# Post-deploy smoke checklist

Run against a controlled candidate account after the target stack is healthy.
Record status, timestamp, request/job IDs, and image digests—never passwords,
OTP codes, tokens, HMAC values, or provider credentials.

## Public edge and application health

- [ ] Gateway returns direct 200 for its configured health endpoint.
- [ ] Marketing, Student Web, Dashboard, and Django API public health routes
  return expected statuses and security headers.
- [ ] Unknown hosts fail closed; former AI hostname returns 404.
- [ ] AI API is not publicly routable; its liveness/readiness route is checked
  only from the private network/container.
- [ ] No public bindings exist for PostgreSQL, Redis, Celery, or AI workers.

## Identity and access

- [ ] Register a controlled user; verify request accepted and OTP task enters
  the critical queue.
- [ ] Verify SMTP handoff and receipt in the controlled mailbox; verify OTP
  invalid/expired/resend behavior without recording a code.
- [ ] Login, refresh, protected request, logout, and post-logout refresh
  rejection pass.
- [ ] Student is denied Dashboard APIs; limited admin is denied ungranted APIs;
  Super Admin retains intended access.

## Sources and AI

- [ ] Upload a supported TXT within the effective plan limit.
- [ ] Reject an oversized and unsupported file with machine-readable errors.
- [ ] Create project-scoped Fahes and Kholasa jobs; verify actual stage updates,
  persisted result, and source grounding.
- [ ] Run Sada on controlled audio; verify one reusable derived text source and
  downstream Fahes/Kholasa use.
- [ ] Verify Khota persisted plan and Rasheed controlled-data analysis.
- [ ] Attempt cross-user/cross-project source access and verify denial.
- [ ] Verify duplicate callback/job retry does not duplicate materialization or
  uncontrolled provider cost.

## Operations

- [ ] Inspect Backend, AI, worker, and gateway logs for unexpected errors.
- [ ] Confirm request/job/task correlation IDs link dispatch through callback.
- [ ] Confirm Redis/Celery queue depth drains and beat schedules intended tasks.
- [ ] Confirm backup job/restore sample and alerting integrations according to
  the operating runbook.
