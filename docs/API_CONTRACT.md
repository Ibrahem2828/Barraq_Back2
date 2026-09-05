# Baraq Backend API Contract v1

Base URL:

```text
https://api.barraq.xn--mgbaab0cxheq.tech/api/v1
```

## System

- `GET /`
- `GET /health/`
- `GET /health/live/`
- `GET /health/ready/`
- `GET /meta/`

## Auth and user

- `GET /auth/`
- `POST /auth/register/`
- `POST /auth/login/`
- `POST /auth/refresh/`
- `POST /auth/verify/`
- `POST /auth/logout/`
- `POST /auth/change-password/`
- `POST /auth/password-reset/`
- `POST /auth/password-reset/confirm/`
- `GET|PATCH /users/me/`

## Student and subjects

- `POST /students/setup-profile/`
- `GET|PATCH /students/profile/`
- `GET /education-stages/`
- `GET /subjects/`
- `GET|POST /users/subjects/`
- `DELETE /users/subjects/{id}/`

## Study plans

- CRUD `/study-plans/`
- `GET|POST /study-plans/{id}/tasks/`
- `GET /study-plans/today/`
- `GET /study-plans/week/`
- CRUD `/study-tasks/`
- `POST /study-tasks/{id}/complete/`
- `POST /study-tasks/{id}/skip/`
- `POST /study-tasks/{id}/reopen/`

## Quizzes

- CRUD `/quizzes/`
- `POST /quizzes/{id}/publish/`
- `POST /quizzes/{id}/start/`
- `POST /quizzes/{id}/archive/`
- CRUD `/quiz-questions/` للـmanual drafts فقط.
- `GET /quiz-attempts/`
- `GET /quiz-attempts/{id}/`
- `POST /quiz-attempts/{id}/answer/`
- `POST /quiz-attempts/{id}/submit/`
- `GET /quiz-attempts/{id}/result/`
- `POST /quiz-attempts/{id}/abandon/`
- `GET /question-bank/`

## Sources

- CRUD `/student-sources/`
- `POST /student-sources/{id}/process/`
- `GET /student-sources/{id}/capabilities/`
- `POST /student-sources/{id}/use-with-character/`
- shortcuts: `use-with-khota`, `use-with-fahes`, `use-with-rasheed`, `use-with-kholasa`, `use-with-sada`.
- CRUD `/student-source-collections/`
- collection actions: `sources`, `capabilities`, and character actions.

## AI jobs

- `GET /ai/`
- `GET /ai/capabilities/`
- `GET /ai/jobs/`
- `POST /ai/jobs/`
- `GET /ai/jobs/{public_id}/`
- `POST /ai/jobs/{public_id}/refresh/`
- `POST /ai/jobs/{public_id}/cancel/`
- `POST /ai/jobs/{public_id}/feedback/`

## Results

- `GET /recommendations/`
- `POST /recommendations/{id}/mark-read/`
- `GET /summaries/`
- `GET /transcriptions/`

## Subscription, notification, support

- `GET /subscriptions/`
- `GET /subscriptions/me/`
- `GET /subscriptions/plans/`
- `GET /notifications/`
- `GET /notifications/unread-count/`
- `POST /notifications/{id}/mark-read/`
- `POST /notifications/mark-all-read/`
- CRUD read/create `/support/tickets/`
- `POST /support/tickets/{id}/messages/`
- `POST /support/tickets/{id}/close/`

## Dashboard

جميع المسارات تحت `/admin/` ومحكومة بـRBAC، وتشمل overview، health، users، roles، permissions، stages، subjects، sources، plans، quizzes، attempts، AI jobs/metrics/feedback/webhooks/results، subscriptions، support، وaudit logs.

## Envelope

الرد النهائي المرسل عبر HTTP:

```json
{
  "success": true,
  "message": "Success",
  "data": {},
  "meta": {},
  "request_id": "uuid-or-id"
}
```

الأخطاء:

```json
{
  "success": false,
  "message": "Validation failed",
  "code": "validation_error",
  "errors": {},
  "request_id": "uuid-or-id"
}
```
