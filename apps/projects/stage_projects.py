"""A project per subject of the learner's education stage, created for them.

Choosing "بكالوريا" should leave the learner with one ready project per
Baccalaureate subject, and a subject the team adds to that stage later (from
the dashboard) should appear for every learner already in it.

Idempotent by construction: a learner never gets two projects for the same
subject, and a project they deleted is not brought back -- soft-deleted rows
count as "already has one".
"""

from __future__ import annotations

from apps.students.models import StudentProfile
from apps.subjects.models import Subject

from .models import Project

AUTO_CONTEXT_SOURCE = "education_stage_subjects"


def _new_project(owner_id, subject) -> Project:
    return Project(
        owner_id=owner_id,
        subject=subject,
        title=subject.name,
        education_context={
            "source": AUTO_CONTEXT_SOURCE,
            "education_stage": subject.education_stage.name,
        },
    )


def ensure_stage_projects(user) -> list[Project]:
    """Create the learner's missing stage projects; returns the ones created."""
    profile = StudentProfile.objects.filter(user=user).select_related("education_stage").first()
    stage = profile.education_stage if profile else None
    if stage is None or not stage.is_active:
        return []
    subjects = list(
        Subject.objects.filter(education_stage=stage, is_active=True)
        .select_related("education_stage")
        .order_by("id")
    )
    taken = set(
        Project.all_objects.filter(owner=user, subject__in=subjects).values_list("subject_id", flat=True)
    )
    missing = [_new_project(user.pk, subject) for subject in subjects if subject.pk not in taken]
    return Project.objects.bulk_create(missing) if missing else []


def add_subject_project_for_stage(subject) -> int:
    """Give every learner in the subject's stage a project for it."""
    if not subject.is_active or not subject.education_stage.is_active:
        return 0
    owners = set(
        StudentProfile.objects.filter(education_stage=subject.education_stage).values_list("user_id", flat=True)
    )
    if not owners:
        return 0
    taken = set(
        Project.all_objects.filter(subject=subject, owner_id__in=owners).values_list("owner_id", flat=True)
    )
    missing = [_new_project(owner_id, subject) for owner_id in sorted(owners - taken)]
    Project.objects.bulk_create(missing)
    return len(missing)
