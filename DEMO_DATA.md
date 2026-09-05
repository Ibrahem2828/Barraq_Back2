# Baraq Demo Data

This file documents the local MVP demo seed for the Baraq backend.

The data is Arabic, educational, non-sensitive, and intended only for local,
staging, or demo environments. Do not use these credentials in production.

## Run

```powershell
python manage.py migrate
python manage.py seed_demo_data
```

To rebuild the demo users and their owned data before reseeding:

```powershell
python manage.py seed_demo_data --reset-demo
```

`--reset-demo` deletes only the users with these demo emails and data owned by
them through normal model cascades. It does not flush the database and does not
delete real users.

## Demo Credentials

Admin:

```text
admin@baraq.app
Admin@123456
```

Project Admin:

```text
project.admin@baraq.app
ProjectAdmin@123456
```

Student:

```text
student@baraq.app
Student@123456
```

Change these credentials before any production deployment.

## Seeded Content

- 3 education stages.
- 11 subjects across preparatory, secondary, and university stages.
- A completed student profile for `student@baraq.app`.
- 4 selected student subjects: mathematics, physics, chemistry, and Arabic.
- 4 study plans.
- 15 study tasks spread across today, tomorrow, the current week, and past days.
- 4 published quizzes.
- 21 questions.
- 66 choices.
- 1 submitted demo quiz attempt for the mathematics quiz.
- 1 ready TXT student source.
- Demo interactions with رشيد, خُطى, and فاحص for that source.

## Endpoints With Demo Data

Public:

- `GET /api/health/`
- `GET /api/education-stages/`
- `GET /api/subjects/`
- `GET /api/subjects/?education_stage=<id>&grade_level=الثالث الثانوي`

Authenticated as `student@baraq.app`:

- `GET /api/users/me/`
- `GET /api/students/profile/`
- `GET /api/users/subjects/`
- `GET /api/study-plans/`
- `GET /api/study-plans/today/`
- `GET /api/study-plans/week/`
- `GET /api/study-plans/{id}/`
- `GET /api/study-plans/{id}/tasks/`
- `POST /api/study-tasks/{id}/complete/`
- `POST /api/study-tasks/{id}/skip/`
- `POST /api/study-tasks/{id}/reopen/`
- `GET /api/quizzes/`
- `GET /api/quizzes/{id}/`
- `POST /api/quizzes/{id}/start/`
- `POST /api/quiz-attempts/{id}/answer/`
- `POST /api/quiz-attempts/{id}/submit/`
- `GET /api/quiz-attempts/{id}/result/`
- `GET /api/student-sources/`
- `GET /api/student-sources/{id}/capabilities/`
- `POST /api/student-sources/{id}/use-with-character/`

## Notes

- The command uses `update_or_create` and `get_or_create` where the models have
  stable natural keys.
- Demo passwords are stored with Django password hashing.
- Quiz detail and in-progress attempt serializers continue to hide correct
  answers; result responses expose grading details through the existing result
  endpoint.
- Django Admin already registers the main MVP models with useful search,
  filters, and list displays.
