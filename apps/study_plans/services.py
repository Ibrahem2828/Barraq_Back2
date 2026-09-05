from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.utils import timezone

from .models import StudyPlan, StudyPlanProgressLog, StudyTask


def _log_progress(user, plan, action, task=None, metadata=None):
    return StudyPlanProgressLog.objects.create(
        user=user,
        plan=plan,
        task=task,
        action=action,
        metadata=metadata or {},
    )


def _build_completion_percentage(completed_tasks, total_tasks):
    if total_tasks == 0:
        return Decimal('0.00')

    return (
        (Decimal(completed_tasks) / Decimal(total_tasks)) * Decimal('100')
    ).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _get_priority_for_difficulty(difficulty_level):
    if difficulty_level == StudyPlan.DifficultyLevel.HARD:
        return StudyTask.Priority.HIGH
    if difficulty_level == StudyPlan.DifficultyLevel.EASY:
        return StudyTask.Priority.LOW
    return StudyTask.Priority.MEDIUM


@transaction.atomic
def create_manual_plan(user, validated_data):
    plan = StudyPlan.objects.create(
        user=user,
        generation_type=StudyPlan.GenerationType.MANUAL,
        status=StudyPlan.Status.ACTIVE,
        **validated_data,
    )
    generate_plan_tasks(plan)
    _log_progress(
        user=user,
        plan=plan,
        action=StudyPlanProgressLog.Action.PLAN_CREATED,
        metadata={'generation_type': StudyPlan.GenerationType.MANUAL},
    )
    update_plan_completion(plan)
    return plan


def generate_plan_tasks(plan):
    tasks = []
    current_date = plan.start_date
    priority = _get_priority_for_difficulty(plan.difficulty_level)
    tasks_per_day = 1 if plan.daily_study_minutes <= 90 else 2

    while current_date <= plan.end_date:
        if tasks_per_day == 1:
            tasks.append(
                StudyTask(
                    plan=plan,
                    title=f"Review {plan.subject.name} concepts",
                    description=(
                        plan.goal
                        or f"Focused study session for {plan.subject.name}."
                    ),
                    task_date=current_date,
                    estimated_minutes=plan.daily_study_minutes,
                    priority=priority,
                    order=1,
                )
            )
        else:
            first_block = max(plan.daily_study_minutes // 2, 1)
            second_block = max(plan.daily_study_minutes - first_block, 1)
            tasks.extend(
                [
                    StudyTask(
                        plan=plan,
                        title=f"Review a topic in {plan.subject.name}",
                        description=(
                            plan.goal
                            or f"Revise key concepts for {plan.subject.name}."
                        ),
                        task_date=current_date,
                        estimated_minutes=first_block,
                        priority=priority,
                        order=1,
                    ),
                    StudyTask(
                        plan=plan,
                        title=f"Practice exercises in {plan.subject.name}",
                        description=(
                            f"Apply today's learning in {plan.subject.name}."
                        ),
                        task_date=current_date,
                        estimated_minutes=second_block,
                        priority=(
                            StudyTask.Priority.HIGH
                            if plan.difficulty_level == StudyPlan.DifficultyLevel.HARD
                            else StudyTask.Priority.MEDIUM
                        ),
                        order=2,
                    ),
                ]
            )

        current_date += timedelta(days=1)

    StudyTask.objects.bulk_create(tasks)
    return tasks


def update_plan_completion(plan):
    total_tasks = plan.tasks.count()
    completed_tasks = plan.tasks.filter(status=StudyTask.Status.COMPLETED).count()
    completion_percentage = _build_completion_percentage(completed_tasks, total_tasks)

    previous_status = plan.status
    plan.completion_percentage = completion_percentage

    if previous_status != StudyPlan.Status.CANCELLED:
        if total_tasks > 0 and completed_tasks == total_tasks:
            plan.status = StudyPlan.Status.COMPLETED
        elif previous_status == StudyPlan.Status.COMPLETED and completed_tasks < total_tasks:
            plan.status = StudyPlan.Status.ACTIVE

    plan.save()

    if (
        previous_status != StudyPlan.Status.COMPLETED
        and plan.status == StudyPlan.Status.COMPLETED
    ):
        _log_progress(
            user=plan.user,
            plan=plan,
            action=StudyPlanProgressLog.Action.PLAN_COMPLETED,
            metadata={
                'completed_tasks': completed_tasks,
                'total_tasks': total_tasks,
            },
        )

    return plan


@transaction.atomic
def update_plan(instance, validated_data):
    previous_status = instance.status

    for field, value in validated_data.items():
        setattr(instance, field, value)

    instance.save()

    if (
        previous_status != StudyPlan.Status.CANCELLED
        and instance.status == StudyPlan.Status.CANCELLED
    ):
        _log_progress(
            user=instance.user,
            plan=instance,
            action=StudyPlanProgressLog.Action.PLAN_CANCELLED,
            metadata={'previous_status': previous_status},
        )
    elif (
        previous_status != StudyPlan.Status.COMPLETED
        and instance.status == StudyPlan.Status.COMPLETED
    ):
        _log_progress(
            user=instance.user,
            plan=instance,
            action=StudyPlanProgressLog.Action.PLAN_COMPLETED,
            metadata={'previous_status': previous_status},
        )

    return instance


@transaction.atomic
def create_plan_task(plan, validated_data):
    task = StudyTask.objects.create(plan=plan, **validated_data)
    update_plan_completion(plan)
    return task


def _set_task_in_progress(task):
    if task.status == StudyTask.Status.IN_PROGRESS and task.completed_at is None:
        return task
    task.status = StudyTask.Status.IN_PROGRESS
    task.completed_at = None
    task.save()
    update_plan_completion(task.plan)
    return task


@transaction.atomic
def update_task(task, validated_data):
    status = validated_data.pop('status', None)

    for field, value in validated_data.items():
        setattr(task, field, value)

    if status is None or status == task.status:
        task.save()
        return task

    task.save()

    if status == StudyTask.Status.COMPLETED:
        return complete_task(task)
    if status == StudyTask.Status.SKIPPED:
        return skip_task(task)
    if status == StudyTask.Status.PENDING:
        return reopen_task(task)
    if status == StudyTask.Status.IN_PROGRESS:
        return _set_task_in_progress(task)

    return task


@transaction.atomic
def delete_task(task):
    plan = task.plan
    task.delete()
    update_plan_completion(plan)


@transaction.atomic
def complete_task(task):
    if task.status == StudyTask.Status.COMPLETED and task.completed_at is not None:
        return task

    previous_status = task.status
    task.status = StudyTask.Status.COMPLETED
    task.completed_at = timezone.now()
    task.save()

    _log_progress(
        user=task.plan.user,
        plan=task.plan,
        task=task,
        action=StudyPlanProgressLog.Action.TASK_COMPLETED,
        metadata={'previous_status': previous_status},
    )
    update_plan_completion(task.plan)
    return task


@transaction.atomic
def skip_task(task):
    if task.status == StudyTask.Status.SKIPPED and task.completed_at is None:
        return task

    previous_status = task.status
    task.status = StudyTask.Status.SKIPPED
    task.completed_at = None
    task.save()

    _log_progress(
        user=task.plan.user,
        plan=task.plan,
        task=task,
        action=StudyPlanProgressLog.Action.TASK_SKIPPED,
        metadata={'previous_status': previous_status},
    )
    update_plan_completion(task.plan)
    return task


@transaction.atomic
def reopen_task(task):
    if task.status == StudyTask.Status.PENDING and task.completed_at is None:
        return task

    previous_status = task.status
    task.status = StudyTask.Status.PENDING
    task.completed_at = None
    task.save()

    _log_progress(
        user=task.plan.user,
        plan=task.plan,
        task=task,
        action=StudyPlanProgressLog.Action.TASK_REOPENED,
        metadata={'previous_status': previous_status},
    )
    update_plan_completion(task.plan)
    return task
