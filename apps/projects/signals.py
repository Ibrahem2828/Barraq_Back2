from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.subjects.models import Subject

from .stage_projects import add_subject_project_for_stage


@receiver(post_save, sender=Subject, dispatch_uid="projects.subject_stage_projects")
def give_stage_learners_a_project(sender, instance, **kwargs):
    # A subject added to (or re-activated in) a stage from the dashboard shows
    # up as a project for every learner already in that stage.
    add_subject_project_for_stage(instance)
