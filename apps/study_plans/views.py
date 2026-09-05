from collections import defaultdict
from datetime import timedelta

from django.utils import timezone
from django.utils.dateparse import parse_date
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema
from rest_framework import filters, mixins, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .permissions import IsStudyPlanOwner
from .selectors import (
    get_today_tasks_queryset,
    get_user_study_plan_queryset,
    get_user_study_task_queryset,
    get_week_tasks_queryset,
)
from .serializers import (
    StudyPlanCreateSerializer,
    StudyPlanDetailSerializer,
    StudyPlanListSerializer,
    StudyPlanUpdateSerializer,
    StudyTaskCreateSerializer,
    StudyTaskSerializer,
    StudyTaskUpdateSerializer,
    TaskStatusUpdateSerializer,
    TodayPlanResponseSerializer,
    WeekPlanResponseSerializer,
)
from .services import complete_task, delete_task, reopen_task, skip_task


def _build_task_summary(tasks):
    total_tasks = len(tasks)
    completed_tasks = sum(1 for task in tasks if task.status == 'completed')
    pending_tasks = sum(
        1 for task in tasks if task.status in {'pending', 'in_progress'}
    )
    total_estimated_minutes = sum(task.estimated_minutes for task in tasks)
    return {
        'total_tasks': total_tasks,
        'completed_tasks': completed_tasks,
        'pending_tasks': pending_tasks,
        'total_estimated_minutes': total_estimated_minutes,
    }


def _group_tasks_by_date(tasks):
    grouped_tasks = defaultdict(list)
    for task in tasks:
        grouped_tasks[task.task_date].append(task)

    days = []
    for task_date in sorted(grouped_tasks.keys()):
        task_group = grouped_tasks[task_date]
        days.append(
            {
                'date': task_date,
                'summary': _build_task_summary(task_group),
                'tasks': task_group,
            }
        )
    return days


@extend_schema(tags=['Study Plans'])
class StudyPlanViewSet(viewsets.ModelViewSet):
    lookup_value_converter = 'int'
    permission_classes = [permissions.IsAuthenticated, IsStudyPlanOwner]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['title', 'goal']
    ordering_fields = ['created_at', 'start_date', 'end_date']
    ordering = ['-created_at']
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_queryset(self):
        queryset = get_user_study_plan_queryset(self.request.user)
        params = self.request.query_params

        status_value = params.get('status')
        if status_value:
            queryset = queryset.filter(status=status_value)

        subject = params.get('subject')
        if subject:
            queryset = queryset.filter(subject_id=subject)

        project = params.get('project')
        if project:
            queryset = queryset.filter(project__public_id=project)

        difficulty_level = params.get('difficulty_level')
        if difficulty_level:
            queryset = queryset.filter(difficulty_level=difficulty_level)

        generation_type = params.get('generation_type')
        if generation_type:
            queryset = queryset.filter(generation_type=generation_type)

        start_date = params.get('start_date')
        if start_date:
            queryset = queryset.filter(start_date=start_date)

        end_date = params.get('end_date')
        if end_date:
            queryset = queryset.filter(end_date=end_date)

        return queryset

    def get_serializer_class(self):
        if self.action == 'list':
            return StudyPlanListSerializer
        if self.action == 'create':
            return StudyPlanCreateSerializer
        if self.action == 'partial_update':
            return StudyPlanUpdateSerializer
        if self.action == 'tasks' and self.request.method == 'POST':
            return StudyTaskCreateSerializer
        if self.action == 'tasks':
            return StudyTaskSerializer
        if self.action == 'today':
            return TodayPlanResponseSerializer
        if self.action == 'week':
            return WeekPlanResponseSerializer
        return StudyPlanDetailSerializer

    @extend_schema(
        description='List the current user study plans with filtering, search, and ordering.',
        parameters=[
            OpenApiParameter(name='status', type=str),
            OpenApiParameter(name='subject', type=int),
            OpenApiParameter(name='project', type=str),
            OpenApiParameter(name='start_date', type=str),
            OpenApiParameter(name='end_date', type=str),
            OpenApiParameter(name='difficulty_level', type=str),
            OpenApiParameter(name='generation_type', type=str),
            OpenApiParameter(name='search', type=str),
            OpenApiParameter(name='ordering', type=str),
        ],
        responses=StudyPlanListSerializer(many=True),
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(
        description='Create a manual study plan. AI plans are created through the AI jobs API.',
        request=StudyPlanCreateSerializer,
        responses={201: StudyPlanDetailSerializer},
        examples=[
            OpenApiExample(
                'Manual Plan Request',
                value={
                    'title': 'Math Final Review Plan',
                    'description': 'A focused review before the final exam.',
                    'subject': 1,
                    'start_date': '2026-05-01',
                    'end_date': '2026-05-05',
                    'daily_study_minutes': 120,
                    'goal': 'Review the core chapters and solve exercises.',
                    'difficulty_level': 'medium',
                    'generation_type': 'manual',
                },
                request_only=True,
            )
        ],
    )
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        plan = serializer.save()
        output_serializer = StudyPlanDetailSerializer(
            plan,
            context=self.get_serializer_context(),
        )
        return Response(output_serializer.data, status=status.HTTP_201_CREATED)

    @extend_schema(
        description='Retrieve one study plan with all of its tasks.',
        responses=StudyPlanDetailSerializer,
    )
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    @extend_schema(
        description='Update editable study plan fields without changing ownership.',
        request=StudyPlanUpdateSerializer,
        responses={200: StudyPlanDetailSerializer},
    )
    def partial_update(self, request, *args, **kwargs):
        plan = self.get_object()
        serializer = self.get_serializer(plan, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        output_serializer = StudyPlanDetailSerializer(
            plan,
            context=self.get_serializer_context(),
        )
        return Response(output_serializer.data)

    @extend_schema(
        description='Delete a study plan belonging to the current user.',
        responses={204: None},
    )
    def destroy(self, request, *args, **kwargs):
        return super().destroy(request, *args, **kwargs)

    @extend_schema(
        methods=['GET'],
        description='List all tasks that belong to a specific study plan.',
        responses=StudyTaskSerializer(many=True),
    )
    @extend_schema(
        methods=['POST'],
        description='Add a manual task to an existing study plan.',
        request=StudyTaskCreateSerializer,
        responses={201: StudyTaskSerializer},
    )
    @action(detail=True, methods=['get', 'post'], url_path='tasks')
    def tasks(self, request, pk=None):
        plan = self.get_object()

        if request.method == 'GET':
            queryset = plan.tasks.select_related(
                'plan',
                'plan__subject',
                'plan__subject__education_stage',
            ).order_by('task_date', 'order', 'id')
            page = self.paginate_queryset(queryset)
            serializer = StudyTaskSerializer(page or queryset, many=True)
            if page is not None:
                return self.get_paginated_response(serializer.data)
            return Response(serializer.data)

        serializer = StudyTaskCreateSerializer(
            data=request.data,
            context={**self.get_serializer_context(), 'plan': plan},
        )
        serializer.is_valid(raise_exception=True)
        task = serializer.save()
        output_serializer = StudyTaskSerializer(task, context=self.get_serializer_context())
        return Response(output_serializer.data, status=status.HTTP_201_CREATED)

    @extend_schema(
        description='Return today tasks for the current user with summary statistics.',
        responses=TodayPlanResponseSerializer,
    )
    @action(detail=False, methods=['get'], url_path='today')
    def today(self, request):
        target_date = timezone.localdate()
        tasks = list(
            get_today_tasks_queryset(request.user, target_date).order_by(
                'task_date',
                'order',
                'id',
            )
        )
        response_data = {
            'date': target_date,
            'summary': _build_task_summary(tasks),
            'tasks': tasks,
        }
        serializer = TodayPlanResponseSerializer(response_data)
        return Response(serializer.data)

    @extend_schema(
        description=(
            'Return a 7-day grouped study view. If start_date is omitted, '
            'the response starts from the beginning of the current week (Monday).'
        ),
        parameters=[OpenApiParameter(name='start_date', type=str)],
        responses=WeekPlanResponseSerializer,
    )
    @action(detail=False, methods=['get'], url_path='week')
    def week(self, request):
        start_date_param = request.query_params.get('start_date')
        if start_date_param:
            week_start = parse_date(start_date_param)
            if week_start is None:
                return Response(
                    {'detail': 'start_date must be a valid ISO date.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            today = timezone.localdate()
            week_start = today - timedelta(days=today.weekday())

        tasks = list(
            get_week_tasks_queryset(request.user, week_start).order_by(
                'task_date',
                'order',
                'id',
            )
        )
        response_data = {
            'start_date': week_start,
            'end_date': week_start + timedelta(days=6),
            'days': _group_tasks_by_date(tasks),
        }
        serializer = WeekPlanResponseSerializer(response_data)
        return Response(serializer.data)


@extend_schema(tags=['Study Plans'])
class StudyTaskViewSet(
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    lookup_value_converter = 'int'
    permission_classes = [permissions.IsAuthenticated, IsStudyPlanOwner]
    http_method_names = ['get', 'patch', 'delete', 'post', 'head', 'options']

    def get_queryset(self):
        return get_user_study_task_queryset(self.request.user).order_by(
            'task_date',
            'order',
            'id',
        )

    def get_serializer_class(self):
        if self.action == 'partial_update':
            return StudyTaskUpdateSerializer
        if self.action in {'complete', 'skip', 'reopen'}:
            return TaskStatusUpdateSerializer
        return StudyTaskSerializer

    @extend_schema(
        description='Retrieve a single task that belongs to the current user.',
        responses=StudyTaskSerializer,
    )
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    @extend_schema(
        description='Update a task fields or status.',
        request=StudyTaskUpdateSerializer,
        responses=StudyTaskSerializer,
    )
    def partial_update(self, request, *args, **kwargs):
        task = self.get_object()
        serializer = self.get_serializer(task, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        task = serializer.save()
        output_serializer = StudyTaskSerializer(task, context=self.get_serializer_context())
        return Response(output_serializer.data)

    @extend_schema(
        description='Delete a task and refresh plan completion statistics.',
        responses={204: None},
    )
    def destroy(self, request, *args, **kwargs):
        task = self.get_object()
        delete_task(task)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        description='Mark a task as completed and update the plan progress.',
        responses=TaskStatusUpdateSerializer,
    )
    @action(detail=True, methods=['post'], url_path='complete')
    def complete(self, request, pk=None):
        task = complete_task(self.get_object())
        serializer = TaskStatusUpdateSerializer(task)
        return Response(serializer.data)

    @extend_schema(
        description='Mark a task as skipped and update the plan progress.',
        responses=TaskStatusUpdateSerializer,
    )
    @action(detail=True, methods=['post'], url_path='skip')
    def skip(self, request, pk=None):
        task = skip_task(self.get_object())
        serializer = TaskStatusUpdateSerializer(task)
        return Response(serializer.data)

    @extend_schema(
        description='Reopen a task by sending it back to pending state.',
        responses=TaskStatusUpdateSerializer,
    )
    @action(detail=True, methods=['post'], url_path='reopen')
    def reopen(self, request, pk=None):
        task = reopen_task(self.get_object())
        serializer = TaskStatusUpdateSerializer(task)
        return Response(serializer.data)
