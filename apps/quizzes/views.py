from django.utils.dateparse import parse_date
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema
from rest_framework import filters, mixins, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import GenerationTypeChoices, Question, QuizStatusChoices
from .permissions import CanAccessQuestionBankItem, IsQuizOwner
from .selectors import (
    get_attempt_detail,
    get_question_bank_items,
    get_user_attempts,
    get_user_quiz_detail,
    get_user_quizzes,
)
from .serializers import (
    QuestionBankItemSerializer,
    QuizAttemptDetailSerializer,
    QuizAttemptSerializer,
    QuizAttemptStartResponseSerializer,
    QuizCreateSerializer,
    QuizDetailSerializer,
    QuizListSerializer,
    QuizQuestionManageSerializer,
    QuizResultSerializer,
    QuizUpdateSerializer,
    StartAttemptSerializer,
    SubmitAnswerSerializer,
    SubmitQuizSerializer,
)
from .services import (
    abandon_attempt,
    archive_quiz,
    build_attempt_result_payload,
    publish_quiz,
    start_quiz_attempt,
    submit_answer,
    submit_quiz_attempt,
)


@extend_schema(tags=['Quizzes'])
class QuizViewSet(viewsets.ModelViewSet):
    lookup_value_converter = 'int'
    permission_classes = [permissions.IsAuthenticated, IsQuizOwner]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['title', 'topic']
    ordering_fields = ['created_at', 'questions_count']
    ordering = ['-created_at']
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_queryset(self):
        queryset = get_user_quizzes(self.request.user)
        params = self.request.query_params

        subject = params.get('subject')
        if subject:
            queryset = queryset.filter(subject_id=subject)

        project = params.get('project')
        if project:
            queryset = queryset.filter(project__public_id=project)

        difficulty_level = params.get('difficulty_level')
        if difficulty_level:
            queryset = queryset.filter(difficulty_level=difficulty_level)

        quiz_type = params.get('quiz_type')
        if quiz_type:
            queryset = queryset.filter(quiz_type=quiz_type)

        generation_type = params.get('generation_type')
        if generation_type:
            queryset = queryset.filter(generation_type=generation_type)

        status_value = params.get('status')
        if status_value:
            queryset = queryset.filter(status=status_value)

        created_at = params.get('created_at')
        if created_at:
            created_date = parse_date(created_at)
            if created_date:
                queryset = queryset.filter(created_at__date=created_date)

        return queryset

    def get_serializer_class(self):
        if self.action == 'list':
            return QuizListSerializer
        if self.action == 'create':
            return QuizCreateSerializer
        if self.action == 'partial_update':
            return QuizUpdateSerializer
        if self.action == 'start':
            return StartAttemptSerializer
        return QuizDetailSerializer

    @extend_schema(
        description='List the current user quizzes with filters, search, and ordering.',
        parameters=[
            OpenApiParameter(name='subject', type=int),
            OpenApiParameter(name='project', type=str),
            OpenApiParameter(name='difficulty_level', type=str),
            OpenApiParameter(name='quiz_type', type=str),
            OpenApiParameter(name='generation_type', type=str),
            OpenApiParameter(name='status', type=str),
            OpenApiParameter(name='created_at', type=str),
            OpenApiParameter(name='search', type=str),
            OpenApiParameter(name='ordering', type=str),
        ],
        responses=QuizListSerializer(many=True),
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(
        description='Create a manual draft quiz. AI quizzes are created through the AI jobs API.',
        request=QuizCreateSerializer,
        responses={201: QuizDetailSerializer},
        examples=[
            OpenApiExample(
                'Quiz Create Request',
                value={
                    'subject': 1,
                    'title': 'اختبار قوانين نيوتن',
                    'description': 'اختبار تدريبي قصير',
                    'topic': 'قوانين نيوتن',
                    'difficulty_level': 'medium',
                    'quiz_type': 'practice',
                    'generation_type': 'manual',
                    'time_limit_minutes': 15,
                    'question_types': ['mcq', 'true_false'],
                },
                request_only=True,
            )
        ],
    )
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        quiz = serializer.save()
        quiz = get_user_quiz_detail(request.user, quiz.id)
        output_serializer = QuizDetailSerializer(quiz, context=self.get_serializer_context())
        return Response(output_serializer.data, status=status.HTTP_201_CREATED)

    @extend_schema(description='Retrieve one quiz without exposing correct answers.')
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    @extend_schema(
        description='Update editable quiz fields without changing ownership.',
        request=QuizUpdateSerializer,
        responses={200: QuizDetailSerializer},
    )
    def partial_update(self, request, *args, **kwargs):
        quiz = self.get_object()
        serializer = self.get_serializer(quiz, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        quiz = get_user_quiz_detail(request.user, quiz.id)
        output_serializer = QuizDetailSerializer(quiz, context=self.get_serializer_context())
        return Response(output_serializer.data)

    @extend_schema(description='Delete a quiz and its related attempts.')
    def destroy(self, request, *args, **kwargs):
        return super().destroy(request, *args, **kwargs)

    @extend_schema(
        request=StartAttemptSerializer,
        responses={201: QuizAttemptStartResponseSerializer},
        description='Start a new quiz attempt for the current user.',
    )
    @action(detail=True, methods=['post'], url_path='start')
    def start(self, request, pk=None):
        quiz = self.get_object()
        attempt = start_quiz_attempt(request.user, quiz)
        refreshed_quiz = get_user_quiz_detail(request.user, quiz.id)
        payload = {
            'attempt_id': attempt.id,
            'quiz': refreshed_quiz,
            'questions': list(refreshed_quiz.questions.all()),
            'started_at': attempt.started_at,
            'time_limit_minutes': refreshed_quiz.time_limit_minutes,
            'status': attempt.status,
        }
        serializer = QuizAttemptStartResponseSerializer(payload)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @extend_schema(
        description='Archive a quiz without deleting it.',
        responses={200: QuizDetailSerializer},
    )
    @action(detail=True, methods=['post'], url_path='archive')
    def archive(self, request, pk=None):
        quiz = archive_quiz(request.user, self.get_object())
        quiz = get_user_quiz_detail(request.user, quiz.id)
        serializer = QuizDetailSerializer(quiz, context=self.get_serializer_context())
        return Response(serializer.data)

    @extend_schema(description='Validate and publish a manual draft quiz.', responses={200: QuizDetailSerializer})
    @action(detail=True, methods=['post'], url_path='publish')
    def publish(self, request, pk=None):
        quiz = publish_quiz(self.get_object())
        quiz = get_user_quiz_detail(request.user, quiz.id)
        return Response(QuizDetailSerializer(quiz, context=self.get_serializer_context()).data)


@extend_schema(tags=['Quiz Questions'])
class QuizQuestionViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = QuizQuestionManageSerializer
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_queryset(self):
        queryset = Question.objects.filter(quiz__user=self.request.user).select_related('quiz').prefetch_related('choices')
        quiz_id = self.request.query_params.get('quiz')
        if quiz_id:
            queryset = queryset.filter(quiz_id=quiz_id)
        return queryset

    def perform_update(self, serializer):
        question = self.get_object()
        if question.quiz.status != QuizStatusChoices.DRAFT or question.quiz.generation_type != GenerationTypeChoices.MANUAL:
            from rest_framework.exceptions import ValidationError
            raise ValidationError('Only manual draft quizzes can be edited.')
        serializer.save()

    def perform_destroy(self, instance):
        if instance.quiz.status != QuizStatusChoices.DRAFT or instance.quiz.generation_type != GenerationTypeChoices.MANUAL:
            from rest_framework.exceptions import ValidationError
            raise ValidationError('Only manual draft quizzes can be edited.')
        quiz = instance.quiz
        instance.delete()
        from .services import _sync_quiz_questions_count
        _sync_quiz_questions_count(quiz)


@extend_schema(tags=['Attempts'])
class QuizAttemptViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    lookup_value_converter = 'int'
    permission_classes = [permissions.IsAuthenticated, IsQuizOwner]
    filter_backends = [filters.OrderingFilter]
    ordering_fields = ['started_at', 'submitted_at', 'percentage']
    ordering = ['-started_at']
    http_method_names = ['get', 'post', 'head', 'options']

    def get_queryset(self):
        queryset = get_user_attempts(self.request.user)
        params = self.request.query_params

        quiz = params.get('quiz')
        if quiz:
            queryset = queryset.filter(quiz_id=quiz)

        status_value = params.get('status')
        if status_value:
            queryset = queryset.filter(status=status_value)

        subject = params.get('subject')
        if subject:
            queryset = queryset.filter(quiz__subject_id=subject)

        return queryset

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return QuizAttemptDetailSerializer
        return QuizAttemptSerializer

    @extend_schema(
        description='List current user quiz attempts.',
        parameters=[
            OpenApiParameter(name='quiz', type=int),
            OpenApiParameter(name='status', type=str),
            OpenApiParameter(name='subject', type=int),
            OpenApiParameter(name='ordering', type=str),
        ],
        responses=QuizAttemptSerializer(many=True),
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(
        description='Retrieve one attempt. In-progress attempts expose questions without correct answers.',
        responses=QuizAttemptDetailSerializer,
    )
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    @extend_schema(
        request=SubmitAnswerSerializer,
        responses={200: QuizAttemptDetailSerializer},
        description='Save or update one answer while the attempt is still in progress.',
    )
    @action(detail=True, methods=['post'], url_path='answer')
    def answer(self, request, pk=None):
        attempt = self.get_object()
        serializer = SubmitAnswerSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        validated = serializer.validated_data
        answer = submit_answer(
            attempt,
            validated['question'],
            selected_choice=validated.get('selected_choice'),
            text_answer=validated.get('text_answer'),
        )
        attempt = get_attempt_detail(request.user, answer.attempt_id)
        output_serializer = QuizAttemptDetailSerializer(
            attempt,
            context=self.get_serializer_context(),
        )
        return Response(output_serializer.data)

    @extend_schema(
        request=SubmitQuizSerializer,
        responses={200: QuizResultSerializer},
        description='Submit a quiz attempt and return the graded result.',
    )
    @action(detail=True, methods=['post'], url_path='submit')
    def submit(self, request, pk=None):
        attempt = self.get_object()
        serializer = SubmitQuizSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        attempt = submit_quiz_attempt(attempt, serializer.validated_data.get('answers', []))
        attempt = get_attempt_detail(request.user, attempt.id)
        result_payload = build_attempt_result_payload(attempt)
        output_serializer = QuizResultSerializer(result_payload)
        return Response(output_serializer.data)

    @extend_schema(
        responses={200: QuizResultSerializer},
        description='Retrieve the graded result for a submitted attempt.',
    )
    @action(detail=True, methods=['get'], url_path='result')
    def result(self, request, pk=None):
        attempt = self.get_object()
        result_payload = build_attempt_result_payload(attempt)
        serializer = QuizResultSerializer(result_payload)
        return Response(serializer.data)

    @extend_schema(
        responses={200: QuizAttemptSerializer},
        description='Mark an in-progress attempt as abandoned.',
    )
    @action(detail=True, methods=['post'], url_path='abandon')
    def abandon(self, request, pk=None):
        attempt = abandon_attempt(self.get_object())
        serializer = QuizAttemptSerializer(attempt, context=self.get_serializer_context())
        return Response(serializer.data)


@extend_schema(tags=['Question Bank'])
class QuestionBankViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    lookup_value_converter = 'int'
    serializer_class = QuestionBankItemSerializer
    permission_classes = [permissions.IsAuthenticated, CanAccessQuestionBankItem]
    http_method_names = ['get', 'head', 'options']

    def get_queryset(self):
        return get_question_bank_items(self.request.user, self.request.query_params)

    @extend_schema(
        description='List public question bank items and private items created by the current user.',
        parameters=[
            OpenApiParameter(name='subject', type=int),
            OpenApiParameter(name='difficulty_level', type=str),
            OpenApiParameter(name='question_type', type=str),
        ],
        responses=QuestionBankItemSerializer(many=True),
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(
        description='Retrieve one question bank item visible to the current user.',
        responses=QuestionBankItemSerializer,
    )
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)
