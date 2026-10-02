"""صفي (My Class): announcements, calendar, assignments and teacher quizzes.

Staff endpoints live under /admin/ (class_work.view / class_work.manage,
scoped like the Classroom Shared Library). Student endpoints are plain
IsAuthenticated + active ClassMembership -- this is not the student's own
content, so TenantScopedQuerysetMixin (owner-scoped) does not apply there.
"""

from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, permissions, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.admin_dashboard.permissions import HasAdminPermission, IsAdminDashboardUser
from apps.organizations import scope as scope_policy

from .models import (
    AssignmentSubmission,
    ClassAnnouncement,
    ClassAssignment,
    ClassEvent,
    ClassQuiz,
    ClassQuizAnswer,
    ClassQuizAttempt,
    ClassQuizChoice,
    ClassQuizQuestion,
)
from .serializers import (
    AssignmentGradeSerializer,
    AssignmentSubmissionSerializer,
    AssignmentSubmitSerializer,
    ClassAnnouncementSerializer,
    ClassAnnouncementWriteSerializer,
    ClassAssignmentSerializer,
    ClassAssignmentWriteSerializer,
    ClassEventSerializer,
    ClassEventWriteSerializer,
    ClassQuizAnswerResultSerializer,
    ClassQuizAnswerSubmitSerializer,
    ClassQuizAttemptSerializer,
    ClassQuizQuestionManageSerializer,
    ClassQuizSerializer,
    ClassQuizStudentSerializer,
    ClassQuizWriteSerializer,
)
from .services import (
    MANAGE_PERMISSION,
    VIEW_PERMISSION,
    grade_written_answer,
    resolve_classroom_for_write,
    scope_rows_for_staff,
    start_class_quiz_attempt,
    student_classroom_ids,
    submit_class_quiz_answer,
    submit_class_quiz_attempt,
)


class _StaffClassWorkViewSet(scope_policy.TenantScopedQuerysetMixin, viewsets.ModelViewSet):
    tenant_user_field = scope_policy.TenantScopedQuerysetMixin.SCOPED_BY_ORGANIZATION
    permission_classes = [IsAdminDashboardUser, HasAdminPermission]
    permission_map = {
        'list': VIEW_PERMISSION, 'retrieve': VIEW_PERMISSION, 'create': MANAGE_PERMISSION,
        'partial_update': MANAGE_PERMISSION, 'destroy': MANAGE_PERMISSION,
        # Custom @action names are NOT in the DRF action set above and fall
        # through to None (fail-closed 403) unless listed explicitly here --
        # see apps/admin_dashboard/views.py's comment on the same trap.
        'submissions': VIEW_PERMISSION, 'attempts': VIEW_PERMISSION,
    }
    required_scope_types = ('global', 'organization', 'class')
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_required_permission(self):
        return self.permission_map.get(getattr(self, 'action', None))


# -- announcements -------------------------------------------------------------
@extend_schema(tags=['Class Work'])
class AdminClassAnnouncementViewSet(_StaffClassWorkViewSet):
    serializer_class = ClassAnnouncementSerializer

    def get_queryset(self):
        queryset = ClassAnnouncement.objects.select_related('classroom', 'created_by')
        return scope_rows_for_staff(self.request.user, queryset, self.get_required_permission())

    def get_serializer_class(self):
        return ClassAnnouncementWriteSerializer if self.action in {'create', 'partial_update'} else ClassAnnouncementSerializer

    def create(self, request, *args, **kwargs):
        serializer = ClassAnnouncementWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        classroom = resolve_classroom_for_write(request.user, serializer.validated_data.pop('classroom'))
        instance = ClassAnnouncement.objects.create(classroom=classroom, created_by=request.user, **serializer.validated_data)
        return Response(ClassAnnouncementSerializer(instance).data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = ClassAnnouncementWriteSerializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.validated_data.pop('classroom', None)  # the classroom a row belongs to never changes
        for field, value in serializer.validated_data.items():
            setattr(instance, field, value)
        instance.save()
        return Response(ClassAnnouncementSerializer(instance).data)


@extend_schema(tags=['Class Work'])
class StudentClassAnnouncementViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ClassAnnouncementSerializer

    def get_queryset(self):
        return ClassAnnouncement.objects.filter(classroom_id__in=student_classroom_ids(self.request.user)).select_related(
            'classroom', 'created_by'
        )


# -- calendar -------------------------------------------------------------------
@extend_schema(tags=['Class Work'])
class AdminClassEventViewSet(_StaffClassWorkViewSet):
    serializer_class = ClassEventSerializer

    def get_queryset(self):
        queryset = ClassEvent.objects.select_related('classroom')
        return scope_rows_for_staff(self.request.user, queryset, self.get_required_permission())

    def get_serializer_class(self):
        return ClassEventWriteSerializer if self.action in {'create', 'partial_update'} else ClassEventSerializer

    def create(self, request, *args, **kwargs):
        serializer = ClassEventWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        classroom = resolve_classroom_for_write(request.user, serializer.validated_data.pop('classroom'))
        instance = ClassEvent.objects.create(classroom=classroom, created_by=request.user, **serializer.validated_data)
        return Response(ClassEventSerializer(instance).data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = ClassEventWriteSerializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.validated_data.pop('classroom', None)
        for field, value in serializer.validated_data.items():
            setattr(instance, field, value)
        instance.save()
        return Response(ClassEventSerializer(instance).data)


@extend_schema(tags=['Class Work'])
class StudentClassEventViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ClassEventSerializer

    def get_queryset(self):
        return ClassEvent.objects.filter(classroom_id__in=student_classroom_ids(self.request.user)).select_related('classroom')


# -- assignments ----------------------------------------------------------------
@extend_schema(tags=['Class Work'])
class AdminClassAssignmentViewSet(_StaffClassWorkViewSet):
    serializer_class = ClassAssignmentSerializer

    def get_queryset(self):
        from django.db.models import Count

        queryset = ClassAssignment.objects.select_related('classroom', 'subject').annotate(
            submissions_count=Count('submissions', distinct=True)
        )
        return scope_rows_for_staff(self.request.user, queryset, self.get_required_permission())

    def get_serializer_class(self):
        return ClassAssignmentWriteSerializer if self.action in {'create', 'partial_update'} else ClassAssignmentSerializer

    def create(self, request, *args, **kwargs):
        serializer = ClassAssignmentWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        classroom = resolve_classroom_for_write(request.user, serializer.validated_data.pop('classroom'))
        instance = ClassAssignment.objects.create(classroom=classroom, created_by=request.user, **serializer.validated_data)
        return Response(ClassAssignmentSerializer(self.get_queryset().get(pk=instance.pk)).data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = ClassAssignmentWriteSerializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.validated_data.pop('classroom', None)
        for field, value in serializer.validated_data.items():
            setattr(instance, field, value)
        instance.save()
        return Response(ClassAssignmentSerializer(self.get_queryset().get(pk=instance.pk)).data)

    @extend_schema(responses=AssignmentSubmissionSerializer(many=True))
    @action(detail=True, methods=['get'])
    def submissions(self, request, pk=None):
        assignment = self.get_object()
        rows = assignment.submissions.select_related('student', 'graded_by').order_by('-submitted_at')
        return Response(AssignmentSubmissionSerializer(rows, many=True).data)


@extend_schema(tags=['Class Work'])
class AdminAssignmentSubmissionViewSet(
    scope_policy.TenantScopedQuerysetMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    """Grading surface: one submission at a time."""

    tenant_user_field = scope_policy.TenantScopedQuerysetMixin.SCOPED_BY_ORGANIZATION
    permission_classes = [IsAdminDashboardUser, HasAdminPermission]
    required_permission = MANAGE_PERMISSION
    required_scope_types = ('global', 'organization', 'class')
    serializer_class = AssignmentSubmissionSerializer

    def get_queryset(self):
        queryset = AssignmentSubmission.objects.select_related('assignment', 'assignment__classroom', 'student')
        return scope_rows_for_staff(
            self.request.user, queryset, self.required_permission, classroom_field='assignment__classroom'
        )

    @extend_schema(request=AssignmentGradeSerializer, responses=AssignmentSubmissionSerializer)
    @action(detail=True, methods=['patch'])
    def grade(self, request, pk=None):
        submission = self.get_object()
        serializer = AssignmentGradeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if data['grade'] > submission.assignment.max_points:
            raise serializers.ValidationError({'grade': 'Grade cannot exceed the assignment\'s maximum points.'})
        submission.grade = data['grade']
        submission.feedback = data.get('feedback', '')
        submission.status = AssignmentSubmission.Status.GRADED
        submission.graded_by = request.user
        submission.graded_at = timezone.now()
        submission.save(update_fields=['grade', 'feedback', 'status', 'graded_by', 'graded_at', 'updated_at'])
        return Response(AssignmentSubmissionSerializer(submission).data)


@extend_schema(tags=['Class Work'])
class StudentClassAssignmentViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ClassAssignmentSerializer

    def get_queryset(self):
        return ClassAssignment.objects.filter(
            classroom_id__in=student_classroom_ids(self.request.user), status=ClassAssignment.Status.PUBLISHED
        ).select_related('classroom', 'subject')

    @extend_schema(request=AssignmentSubmitSerializer, responses={201: AssignmentSubmissionSerializer})
    @action(detail=True, methods=['post'])
    def submit(self, request, pk=None):
        assignment = self.get_object()
        if assignment.due_at and timezone.now() > assignment.due_at and not assignment.allow_late:
            return Response({'detail': 'This assignment no longer accepts submissions.'}, status=status.HTTP_409_CONFLICT)
        serializer = AssignmentSubmitSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        late = bool(assignment.due_at and timezone.now() > assignment.due_at)
        defaults = {
            'text_response': data.get('text_response', ''),
            'submitted_at': timezone.now(),
            'status': AssignmentSubmission.Status.LATE if late else AssignmentSubmission.Status.SUBMITTED,
            'grade': None,
            'graded_at': None,
        }
        uploaded = data.get('file')
        if uploaded:
            meta = serializer.context['file_metadata']
            defaults.update(
                file=uploaded, original_filename=meta['original_filename'], file_size=meta['file_size'],
                mime_type=meta['mime_type'], extension=meta['extension'],
            )
        submission, _created = AssignmentSubmission.objects.update_or_create(
            assignment=assignment, student=request.user, defaults=defaults
        )
        return Response(AssignmentSubmissionSerializer(submission).data, status=status.HTTP_201_CREATED)

    @extend_schema(responses=AssignmentSubmissionSerializer)
    @action(detail=True, methods=['get'], url_path='my-submission')
    def my_submission(self, request, pk=None):
        assignment = self.get_object()
        submission = get_object_or_404(AssignmentSubmission, assignment=assignment, student=request.user)
        return Response(AssignmentSubmissionSerializer(submission).data)


# -- class quizzes: authoring ----------------------------------------------------
@extend_schema(tags=['Class Work'])
class AdminClassQuizViewSet(_StaffClassWorkViewSet):
    serializer_class = ClassQuizSerializer

    def get_queryset(self):
        queryset = ClassQuiz.objects.select_related('classroom', 'subject').prefetch_related('questions__choices')
        return scope_rows_for_staff(self.request.user, queryset, self.get_required_permission())

    def get_serializer_class(self):
        return ClassQuizWriteSerializer if self.action in {'create', 'partial_update'} else ClassQuizSerializer

    def create(self, request, *args, **kwargs):
        serializer = ClassQuizWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        classroom = resolve_classroom_for_write(request.user, serializer.validated_data.pop('classroom'))
        instance = ClassQuiz.objects.create(classroom=classroom, created_by=request.user, **serializer.validated_data)
        return Response(ClassQuizSerializer(self.get_queryset().get(pk=instance.pk)).data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.status == ClassQuiz.Status.PUBLISHED and request.data.get('status') not in (None, 'archived'):
            # A published quiz's content is frozen: attempts may already
            # exist against it. Archiving (to stop new attempts) is still
            # allowed; editing the questions underneath them is not.
            allowed_fields = {'status'}
            if set(request.data) - allowed_fields:
                raise serializers.ValidationError('A published quiz can only be archived, not edited.')
        serializer = ClassQuizWriteSerializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.validated_data.pop('classroom', None)
        for field, value in serializer.validated_data.items():
            setattr(instance, field, value)
        instance.save()
        return Response(ClassQuizSerializer(self.get_queryset().get(pk=instance.pk)).data)

    @extend_schema(responses=ClassQuizAttemptSerializer(many=True))
    @action(detail=True, methods=['get'])
    def attempts(self, request, pk=None):
        quiz = self.get_object()
        rows = quiz.attempts.select_related('student').order_by('-started_at')
        return Response(ClassQuizAttemptSerializer(rows, many=True).data)


@extend_schema(tags=['Class Work'])
class AdminClassQuizQuestionViewSet(scope_policy.TenantScopedQuerysetMixin, viewsets.ModelViewSet):
    # Scoped by hand in get_queryset() via the quiz's classroom (traversing
    # class_quiz__classroom), so filter_queryset() has nothing left to do --
    # SCOPED_BY_ORGANIZATION declares that truthfully instead of silently.
    tenant_user_field = scope_policy.TenantScopedQuerysetMixin.SCOPED_BY_ORGANIZATION
    permission_classes = [IsAdminDashboardUser, HasAdminPermission]
    required_permission = MANAGE_PERMISSION
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']
    serializer_class = ClassQuizQuestionManageSerializer

    def get_queryset(self):
        queryset = ClassQuizQuestion.objects.select_related('class_quiz__classroom').prefetch_related('choices')
        return scope_rows_for_staff(
            self.request.user, queryset, self.required_permission, classroom_field='class_quiz__classroom'
        )

    def create(self, request, *args, **kwargs):
        serializer = ClassQuizQuestionManageSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()
        return Response(ClassQuizQuestionManageSerializer(instance).data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.class_quiz.status == ClassQuiz.Status.PUBLISHED:
            raise serializers.ValidationError('A published quiz can no longer have its questions edited.')
        serializer = ClassQuizQuestionManageSerializer(
            instance, data=request.data, partial=True, context={'request': request}
        )
        serializer.is_valid(raise_exception=True)
        serializer.validated_data.pop('class_quiz', None)
        instance = serializer.update(instance, serializer.validated_data)
        return Response(ClassQuizQuestionManageSerializer(instance).data)


@extend_schema(tags=['Class Work'])
class AdminClassQuizAnswerViewSet(scope_policy.TenantScopedQuerysetMixin, viewsets.GenericViewSet):
    tenant_user_field = scope_policy.TenantScopedQuerysetMixin.SCOPED_BY_ORGANIZATION
    permission_classes = [IsAdminDashboardUser, HasAdminPermission]
    required_permission = MANAGE_PERMISSION
    serializer_class = ClassQuizAnswerResultSerializer

    def get_queryset(self):
        queryset = ClassQuizAnswer.objects.select_related('attempt__class_quiz__classroom', 'question')
        return scope_rows_for_staff(
            self.request.user, queryset, self.required_permission, classroom_field='attempt__class_quiz__classroom'
        )

    @extend_schema(responses=ClassQuizAnswerResultSerializer)
    @action(detail=True, methods=['patch'], url_path='grade')
    def grade(self, request, pk=None):
        answer = get_object_or_404(self.get_queryset(), pk=pk)
        points = request.data.get('points_awarded')
        if points is None:
            raise serializers.ValidationError({'points_awarded': 'This field is required.'})
        grade_written_answer(answer, points_awarded=points, teacher=request.user)
        answer.refresh_from_db()
        return Response(ClassQuizAnswerResultSerializer(answer).data)


# -- class quizzes: taking them ---------------------------------------------------
@extend_schema(tags=['Class Work'])
class StudentClassQuizViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ClassQuizStudentSerializer

    def get_queryset(self):
        return ClassQuiz.objects.filter(
            classroom_id__in=student_classroom_ids(self.request.user), status=ClassQuiz.Status.PUBLISHED
        ).select_related('classroom', 'subject')

    @extend_schema(responses={201: ClassQuizAttemptSerializer})
    @action(detail=True, methods=['post'])
    def start(self, request, pk=None):
        quiz = self.get_object()
        attempt = start_class_quiz_attempt(request.user, quiz)
        return Response(ClassQuizAttemptSerializer(attempt).data, status=status.HTTP_201_CREATED)


@extend_schema(tags=['Class Work'])
class StudentClassQuizAttemptViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ClassQuizAttemptSerializer

    def get_queryset(self):
        return ClassQuizAttempt.objects.filter(student=self.request.user).select_related('class_quiz')

    @extend_schema(request=ClassQuizAnswerSubmitSerializer, responses=ClassQuizAnswerResultSerializer)
    @action(detail=True, methods=['post'])
    def answer(self, request, pk=None):
        attempt = self.get_object()
        serializer = ClassQuizAnswerSubmitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        question = get_object_or_404(ClassQuizQuestion, pk=data['question'], class_quiz_id=attempt.class_quiz_id)
        selected_choice = None
        if data.get('selected_choice'):
            selected_choice = get_object_or_404(ClassQuizChoice, pk=data['selected_choice'])
        answer = submit_class_quiz_answer(
            attempt, question, selected_choice=selected_choice, text_answer=data.get('text_answer', '')
        )
        return Response(ClassQuizAnswerResultSerializer(answer).data)

    @extend_schema(responses=ClassQuizAttemptSerializer)
    @action(detail=True, methods=['post'])
    def submit(self, request, pk=None):
        attempt = self.get_object()
        submit_class_quiz_attempt(attempt)
        return Response(ClassQuizAttemptSerializer(attempt).data)

    @extend_schema(responses=ClassQuizAnswerResultSerializer(many=True))
    @action(detail=True, methods=['get'])
    def result(self, request, pk=None):
        attempt = self.get_object()
        if attempt.status != ClassQuizAttempt.Status.SUBMITTED:
            return Response({'detail': 'Submit the attempt first.'}, status=status.HTTP_409_CONFLICT)
        rows = attempt.answers.select_related('question').order_by('question__order')
        return Response(ClassQuizAnswerResultSerializer(rows, many=True).data)
