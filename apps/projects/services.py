from __future__ import annotations

from .models import ProjectActivity


def record_project_activity(*, project, event_type, actor=None, request_id="", artifact=None, metadata=None):
    """Write a small, safe timeline record for a project domain event."""

    return ProjectActivity.objects.create(
        project=project,
        actor=actor if getattr(actor, "is_authenticated", False) else actor,
        event_type=event_type,
        request_id=str(request_id or "")[:128],
        artifact_type=artifact.__class__.__name__ if artifact is not None else "",
        artifact_id=str(getattr(artifact, "pk", "") or ""),
        metadata=metadata or {},
    )
