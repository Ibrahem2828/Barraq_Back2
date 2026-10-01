"""Classroom Shared Library: who sees an item, and how a student uses one.

Two boundaries, decided here and nowhere else:

* **Staff** reach items through their admin scope (`library.view` /
  `library.manage`): an organization grant covers the organization's shared
  items and every class's items; a class grant covers that class's items
  only -- never the school-wide shelf, which belongs to the organization.
* **Students** reach items through active membership: their classes' items,
  plus their organizations' shared items. No membership, no library.

A student never runs a character on the shared file itself. `copy_for_student`
places a private copy in one of the student's own projects, so every
existing AI rule (owner-only sources, one project per job, version pinning)
applies unchanged. Copies are not uploads: they do not consume the student's
upload or storage quota, and a repeated use returns the same copy.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from django.core.files import File
from django.db import transaction
from django.db.models import Q
from rest_framework.exceptions import ValidationError

from . import scope as scope_policy
from .models import ClassLibraryItem, ClassMembership, Classroom, Organization, OrganizationMembership

VIEW_PERMISSION = "library.view"
MANAGE_PERMISSION = "library.manage"

#: Characters that read a library item, by the kind of file it is. Rasheed
#: analyses quiz results rather than files, so he has no file to receive.
DOCUMENT_CHARACTERS = ("fahes", "kholasa", "khota")
AUDIO_CHARACTERS = ("sada",)


def characters_for(item: ClassLibraryItem) -> tuple[str, ...]:
    if item.source_type == "audio":
        return AUDIO_CHARACTERS
    if item.source_type in {"pdf", "text", "document", "presentation"}:
        return DOCUMENT_CHARACTERS
    return ()


# -- staff --------------------------------------------------------------------
def scope_items_for_staff(user, queryset, permission):
    organizations = scope_policy._normalize(scope_policy.accessible_organization_ids(user, permission))
    if scope_policy.is_unrestricted(organizations):
        return queryset
    classrooms = scope_policy._normalize(scope_policy.accessible_classroom_ids(user, permission))
    class_ids = [] if scope_policy.is_unrestricted(classrooms) else list(classrooms or [])
    return queryset.filter(
        Q(organization_id__in=list(organizations or []))
        | Q(classroom_id__in=class_ids)
    )


def resolve_upload_target(user, organization_public_id, classroom_public_id):
    """Where a new item goes, checked against the uploader's reach.

    A class supervisor must name one of its classes; only an organization
    grant may publish to the whole organization. Unknown and out-of-scope
    targets read the same, so neither confirms a tenant to an outsider.
    """
    classroom = None
    if classroom_public_id:
        classroom = Classroom.objects.select_related("organization").filter(public_id=classroom_public_id).first()
        if classroom is None or classroom.status != Classroom.Status.ACTIVE:
            raise ValidationError({"classroom": "Unknown class."})
        try:
            scope_policy.assert_classroom_allowed(user, classroom, MANAGE_PERMISSION)
        except scope_policy.ScopeDenied as exc:
            raise ValidationError({"classroom": "Unknown class."}) from exc
        organization = classroom.organization
        if organization_public_id and str(organization.public_id) != str(organization_public_id):
            raise ValidationError({"classroom": "The class must belong to the chosen organization."})
    else:
        organization = Organization.objects.filter(public_id=organization_public_id).first() if organization_public_id else None
        if organization is None:
            raise ValidationError({"organization": "Choose the organization or the class this file is for."})
        try:
            scope_policy.assert_organization_allowed(user, organization, MANAGE_PERMISSION)
        except scope_policy.ScopeDenied as exc:
            raise ValidationError(
                {"organization": "Only an organization manager can share a file with the whole organization."}
            ) from exc
    if organization.status != Organization.Status.ACTIVE:
        raise ValidationError({"organization": "Unknown organization."})
    return organization, classroom


def sha256_of(file_field) -> str:
    digest = hashlib.sha256()
    with file_field.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# -- students -----------------------------------------------------------------
def student_reach(user):
    organization_ids = set(
        OrganizationMembership.objects.filter(
            user=user,
            status=OrganizationMembership.Status.ACTIVE,
            organization__status=Organization.Status.ACTIVE,
        ).values_list("organization_id", flat=True)
    )
    classroom_ids = set(
        ClassMembership.objects.filter(
            user=user,
            status=ClassMembership.Status.ACTIVE,
            classroom__status=Classroom.Status.ACTIVE,
            classroom__organization__status=Organization.Status.ACTIVE,
        ).values_list("classroom_id", flat=True)
    )
    return organization_ids, classroom_ids


def items_for_student(user):
    organization_ids, classroom_ids = student_reach(user)
    if not organization_ids and not classroom_ids:
        return ClassLibraryItem.objects.none()
    return (
        ClassLibraryItem.objects.filter(status=ClassLibraryItem.Status.ACTIVE)
        .filter(
            Q(classroom_id__in=classroom_ids)
            | Q(classroom__isnull=True, organization_id__in=organization_ids)
        )
        .select_related("organization", "classroom", "subject")
    )


def _project_for(user, item, project_public_id):
    from apps.projects.models import Project

    projects = Project.objects.filter(owner=user, status=Project.Status.ACTIVE)
    if project_public_id:
        project = projects.filter(public_id=project_public_id).first()
        if project is None:
            raise ValidationError({"project": "Project not found."})
        return project
    if item.subject_id:
        # The learner's own project for this subject (stage projects exist
        # per subject), so the copy sits beside their other material for it.
        project = projects.filter(subject_id=item.subject_id).order_by("created_at").first()
        if project is not None:
            return project
    marker = str(item.classroom.public_id if item.classroom_id else item.organization.public_id)
    project = projects.filter(education_context__class_library=marker).first()
    if project is None:
        place = item.classroom.name if item.classroom_id else item.organization.name
        project = Project.objects.create(
            owner=user,
            title=f"مكتبة {place}"[:255],
            subject=item.subject,
            goal="ملفات مشاركة من مكتبة الشعبة",
            education_context={"class_library": marker},
        )
    return project


def copy_for_student(user, item: ClassLibraryItem, *, project_public_id=None):
    """The student's own copy of a library item, created once per project.

    Returns (source, created). Not an upload: no quota is consumed. The file
    is copied server-side, so the student never downloads and re-uploads it.
    """
    from apps.sources.models import StudentSource
    from apps.sources.tasks import process_source_task

    with transaction.atomic():
        # Serialize a learner's concurrent first uses (two taps, two tabs):
        # without a lock both would miss the existing copy and create one.
        type(user).objects.select_for_update().filter(pk=user.pk).first()
        project = _project_for(user, item, project_public_id)
        existing = (
            StudentSource.objects.filter(
                user=user,
                project=project,
                metadata__library_item=str(item.public_id),
                metadata__library_sha256=item.sha256,
            )
            .exclude(status=StudentSource.Status.FAILED)
            .first()
        )
        if existing is not None:
            return existing, False
        source = StudentSource(
            user=user,
            project=project,
            subject=item.subject,
            title=item.title,
            description=item.description,
            source_type=item.source_type,
            original_filename=item.original_filename,
            file_size=item.file_size,
            mime_type=item.mime_type,
            extension=item.extension,
            metadata={
                "library_item": str(item.public_id),
                "library_sha256": item.sha256,
                "derived_from": "class_library",
                "organization": str(item.organization.public_id),
            },
        )
        with item.file.open("rb") as handle:
            source.file.save(Path(item.original_filename).name or f"library{item.extension}", File(handle), save=False)
        source.save()
        transaction.on_commit(lambda: process_source_task.delay(source.pk))
    return source, True


# -- notifications ------------------------------------------------------------
def notify_students(item: ClassLibraryItem):
    """Tell the item's students it is there -- once per item and student."""
    from apps.notifications.models import Notification

    user_ids: set[int]
    if item.classroom is not None:
        user_ids = set(
            ClassMembership.objects.filter(
                classroom=item.classroom, status=ClassMembership.Status.ACTIVE
            ).values_list("user_id", flat=True)
        )
        place = item.classroom.name
    else:
        user_ids = set(
            OrganizationMembership.objects.filter(
                organization_id=item.organization_id,
                status=OrganizationMembership.Status.ACTIVE,
                member_type=OrganizationMembership.MemberType.STUDENT,
            ).values_list("user_id", flat=True)
        )
        place = item.organization.name
    key = f"class-library:{item.public_id}"
    already = set(Notification.objects.filter(idempotency_key=key).values_list("user_id", flat=True))
    Notification.objects.bulk_create(
        [
            Notification(
                user_id=user_id,
                category=Notification.Category.STUDY,
                title="ملف جديد في مكتبة الشعبة",
                body=f"أُضيف «{item.title}» إلى مكتبة {place}.",
                data={"library_item": str(item.public_id)},
                action_url="/library?tab=class",
                idempotency_key=key,
            )
            for user_id in user_ids - already
        ],
        ignore_conflicts=True,
    )
