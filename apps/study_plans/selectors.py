from datetime import timedelta

from django.db.models import Count, Prefetch, Q
from django.utils import timezone

from .models import StudyPlan, StudyTask


def get_study_task_base_queryset():
    return StudyTask.objects.select_related(
        'plan',
        'plan__subject',
        'plan__subject__education_stage',
    )


def get_user_study_task_queryset(user):
    return get_study_task_base_queryset().filter(plan__user=user)


def get_study_plan_base_queryset():
    task_queryset = StudyTask.objects.order_by('task_date', 'order', 'id')
    return (
        StudyPlan.objects.select_related(
            'user',
            'subject',
            'subject__education_stage',
        )
        .prefetch_related(Prefetch('tasks', queryset=task_queryset))
        .annotate(
            total_tasks=Count('tasks', distinct=True),
            completed_tasks=Count(
                'tasks',
                filter=Q(tasks__status=StudyTask.Status.COMPLETED),
                distinct=True,
            ),
        )
    )


def get_user_study_plan_queryset(user):
    return get_study_plan_base_queryset().filter(user=user)


def get_today_tasks_queryset(user, target_date=None):
    target_date = target_date or timezone.localdate()
    return get_user_study_task_queryset(user).filter(task_date=target_date)


def get_week_tasks_queryset(user, start_date):
    end_date = start_date + timedelta(days=6)
    return get_user_study_task_queryset(user).filter(
        task_date__range=(start_date, end_date)
    )
