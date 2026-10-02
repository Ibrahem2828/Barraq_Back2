"""رحلة برّاق: student-facing progress/unlocks/challenges, plus the two
authoring surfaces -- platform content (journey.view/journey.manage, for
content_manager and super_admin) and a class's shared goal (reusing
apps.class_work's class_work.view/class_work.manage, since a ClassGoal is
that same "one class's shared work" concern, not a new kind of tenant).
"""

from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, permissions, serializers, status, viewsets
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.admin_dashboard.permissions import HasAdminPermission, IsAdminDashboardUser
from apps.class_work.services import MANAGE_PERMISSION as CLASS_WORK_MANAGE_PERMISSION
from apps.class_work.services import VIEW_PERMISSION as CLASS_WORK_VIEW_PERMISSION
from apps.class_work.services import resolve_classroom_for_write, scope_rows_for_staff, student_classroom_ids
from apps.organizations import scope as scope_policy

from .models import ClassGoal, PathStation, SubjectPath, Unlockable, UnlockedItem, WeeklyChallenge
from .serializers import (
    ClassGoalSerializer,
    ClassGoalWriteSerializer,
    JourneyHomeSerializer,
    PathStationWriteSerializer,
    SubjectPathSerializer,
    UnlockableSerializer,
    UnlockableWriteSerializer,
    WeeklyChallengeSerializer,
    WeeklyChallengeWriteSerializer,
)
from .services import complete_station, student_journey, total_gems

VIEW_PERMISSION = 'journey.view'
MANAGE_PERMISSION = 'journey.manage'


# -- student-facing ------------------------------------------------------------
@extend_schema(tags=['Journey'])
class JourneyHomeView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(responses=JourneyHomeSerializer)
    def get(self, request):
        rows = student_journey(request.user)
        payload = {
            'total_gems': total_gems(request.user),
            'subjects': [
                {'subject': row['subject'], 'gem_fragments_total': row['gem_fragments_total'], 'stations': row['stations']}
                for row in rows
            ],
        }
        return Response(JourneyHomeSerializer(payload).data)


@extend_schema(tags=['Journey'])
class CompleteStationView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(request=None, responses={200: JourneyHomeSerializer})
    def post(self, request, station_id):
        station = get_object_or_404(PathStation.objects.select_related('path__subject'), pk=station_id, is_active=True)
        complete_station(request.user, station)
        rows = student_journey(request.user)
        payload = {'total_gems': total_gems(request.user), 'subjects': rows}
        return Response(JourneyHomeSerializer(payload).data)


@extend_schema(tags=['Journey'])
class UnlockableListView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(responses=UnlockableSerializer(many=True))
    def get(self, request):
        unlocked_ids = set(UnlockedItem.objects.filter(user=request.user).values_list('unlockable_id', flat=True))
        rows = Unlockable.objects.filter(is_active=True).order_by('gem_requirement')
        data = []
        for item in rows:
            serialized = UnlockableSerializer(item).data
            serialized['unlocked'] = item.id in unlocked_ids
            data.append(serialized)
        return Response(data)


@extend_schema(tags=['Journey'])
class WeeklyChallengeListView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(responses=WeeklyChallengeSerializer(many=True))
    def get(self, request):
        today = timezone.localdate()
        week_start = today - timezone.timedelta(days=today.weekday())
        rows = WeeklyChallenge.objects.filter(is_active=True, week_start=week_start)
        return Response(WeeklyChallengeSerializer(rows, many=True, context={'request': request}).data)


# -- platform content authoring (journey.view / journey.manage) --------------
class _JourneyContentViewSet(scope_policy.TenantScopedQuerysetMixin, viewsets.ModelViewSet):
    # Platform content (paths, stations, unlockables, challenges), shared
    # by every tenant -- the same declaration apps.subjects' curriculum
    # catalog uses.
    tenant_user_field = None
    permission_classes = [IsAdminDashboardUser, HasAdminPermission]
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_required_permission(self):
        return MANAGE_PERMISSION if self.action in {'create', 'partial_update', 'destroy'} else VIEW_PERMISSION


@extend_schema(tags=['Journey'])
class AdminSubjectPathViewSet(_JourneyContentViewSet):
    queryset = SubjectPath.objects.select_related('subject').prefetch_related('stations')
    serializer_class = SubjectPathSerializer

    def create(self, request, *args, **kwargs):
        subject_id = request.data.get('subject')
        if not subject_id:
            raise serializers.ValidationError({'subject': 'This field is required.'})
        path, _created = SubjectPath.objects.get_or_create(subject_id=subject_id)
        return Response(SubjectPathSerializer(path).data, status=status.HTTP_201_CREATED)


@extend_schema(tags=['Journey'])
class AdminPathStationViewSet(_JourneyContentViewSet):
    queryset = PathStation.objects.select_related('path')
    serializer_class = PathStationWriteSerializer


@extend_schema(tags=['Journey'])
class AdminUnlockableViewSet(_JourneyContentViewSet):
    queryset = Unlockable.objects.all()
    serializer_class = UnlockableWriteSerializer


@extend_schema(tags=['Journey'])
class AdminWeeklyChallengeViewSet(_JourneyContentViewSet):
    queryset = WeeklyChallenge.objects.all()
    serializer_class = WeeklyChallengeWriteSerializer


# -- class goals (reuses apps.class_work's permission codes) -----------------
@extend_schema(tags=['Journey'])
class AdminClassGoalViewSet(scope_policy.TenantScopedQuerysetMixin, viewsets.ModelViewSet):
    tenant_user_field = scope_policy.TenantScopedQuerysetMixin.SCOPED_BY_ORGANIZATION
    permission_classes = [IsAdminDashboardUser, HasAdminPermission]
    permission_map = {
        'list': CLASS_WORK_VIEW_PERMISSION, 'retrieve': CLASS_WORK_VIEW_PERMISSION,
        'create': CLASS_WORK_MANAGE_PERMISSION, 'partial_update': CLASS_WORK_MANAGE_PERMISSION,
        'destroy': CLASS_WORK_MANAGE_PERMISSION,
    }
    required_scope_types = ('global', 'organization', 'class')
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']
    serializer_class = ClassGoalSerializer

    def get_required_permission(self):
        return self.permission_map.get(getattr(self, 'action', None))

    def get_queryset(self):
        queryset = ClassGoal.objects.select_related('classroom')
        return scope_rows_for_staff(self.request.user, queryset, self.get_required_permission())

    def get_serializer_class(self):
        return ClassGoalWriteSerializer if self.action in {'create', 'partial_update'} else ClassGoalSerializer

    def create(self, request, *args, **kwargs):
        serializer = ClassGoalWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        classroom = resolve_classroom_for_write(request.user, serializer.validated_data.pop('classroom'))
        instance = ClassGoal.objects.create(classroom=classroom, created_by=request.user, **serializer.validated_data)
        return Response(ClassGoalSerializer(instance).data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = ClassGoalWriteSerializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.validated_data.pop('classroom', None)
        for field, value in serializer.validated_data.items():
            setattr(instance, field, value)
        if instance.progress_value >= instance.target_value:
            instance.is_completed = True
        instance.save()
        return Response(ClassGoalSerializer(instance).data)


@extend_schema(tags=['Journey'])
class StudentClassGoalViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """A student's own classes' shared goals -- membership, not ownership."""

    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ClassGoalSerializer

    def get_queryset(self):
        return ClassGoal.objects.filter(classroom_id__in=student_classroom_ids(self.request.user)).select_related('classroom')
