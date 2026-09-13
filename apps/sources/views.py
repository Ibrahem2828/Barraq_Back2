from django.db import transaction
from django.http import FileResponse, Http404
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import filters, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from apps.ai_integration.serializers import AIJobSerializer
from apps.quizzes.selectors import get_user_quiz_detail
from apps.quizzes.serializers import QuizListSerializer
from apps.study_plans.selectors import get_user_study_plan_queryset
from apps.study_plans.serializers import StudyPlanListSerializer
from apps.subscriptions.services import (
    can_create_collection,
    can_upload_source,
    consume_collection_created,
    consume_source_uploaded,
    get_user_features,
)

from .capabilities import (
    get_collection_character_capabilities,
    get_source_character_capabilities,
)
from .models import StudentSource, StudentSourceCollection, StudentSourceInteraction
from .serializers import (
    SourceCharacterResponseSerializer,
    StudentSourceBriefSerializer,
    StudentSourceCollectionCreateUpdateSerializer,
    StudentSourceCollectionDetailSerializer,
    StudentSourceCollectionListSerializer,
    StudentSourceCreateSerializer,
    StudentSourceDetailSerializer,
    StudentSourceInteractionSerializer,
    StudentSourceListSerializer,
    StudentSourceUpdateSerializer,
    UseWithCharacterSerializer,
)
from .services import use_collection_with_character, use_source_with_character
from .tasks import process_source_task


@extend_schema(tags=['Student Sources'])
class StudentSourceViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['title', 'description', 'original_filename']
    ordering_fields = ['created_at', 'updated_at', 'file_size']
    ordering = ['-created_at']
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_throttles(self):
        ai_actions = {'use_with_character', 'use_with_khota', 'use_with_fahes', 'use_with_rasheed', 'use_with_kholasa', 'use_with_sada'}
        self.throttle_scope = 'uploads' if self.action in {'create', 'process'} else ('ai_requests' if self.action in ai_actions else None)
        return super().get_throttles()

    def get_queryset(self):
        queryset = StudentSource.objects.select_related(
            'user',
            'subject',
            'subject__education_stage',
            'collection',
            'project',
        ).filter(user=self.request.user)

        source_type = self.request.query_params.get('source_type')
        if source_type:
            queryset = queryset.filter(source_type=source_type)

        status_value = self.request.query_params.get('status')
        if status_value:
            queryset = queryset.filter(status=status_value)

        subject = self.request.query_params.get('subject')
        if subject:
            queryset = queryset.filter(subject_id=subject)

        collection = self.request.query_params.get('collection')
        if collection:
            queryset = queryset.filter(collection_id=collection)

        project = self.request.query_params.get('project')
        if project:
            queryset = queryset.filter(project__public_id=project)

        return queryset

    def get_serializer_class(self):
        if self.action == 'list':
            return StudentSourceListSerializer
        if self.action == 'create':
            return StudentSourceCreateSerializer
        if self.action == 'partial_update':
            return StudentSourceUpdateSerializer
        if self.action == 'use_with_character':
            return UseWithCharacterSerializer
        return StudentSourceDetailSerializer

    @extend_schema(
        parameters=[
            OpenApiParameter(name='source_type', type=str),
            OpenApiParameter(name='status', type=str),
            OpenApiParameter(name='subject', type=int),
            OpenApiParameter(name='collection', type=int),
            OpenApiParameter(name='project', type=str),
        ],
        responses=StudentSourceListSerializer(many=True),
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(
        request=StudentSourceCreateSerializer,
        responses={201: StudentSourceDetailSerializer},
        description='Upload a student source using multipart/form-data.',
    )
    def create(self, request, *args, **kwargs):
        if not request.data.get('title'):
            raise ValidationError({'title': 'يرجى إدخال عنوان للمصدر.'})
        if request.FILES.get('file') is None:
            raise ValidationError({'file': 'يرجى اختيار ملف لرفعه.'})
        with transaction.atomic():
            locked_user = request.user.__class__.objects.select_for_update().get(pk=request.user.pk)
            can_upload_source(locked_user, request.FILES['file'].size)
            serializer = self.get_serializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            source = serializer.save()
            consume_source_uploaded(locked_user, source.file_size)
            transaction.on_commit(lambda: process_source_task.delay(source.pk))
        output_serializer = StudentSourceDetailSerializer(
            source,
            context=self.get_serializer_context(),
        )
        return Response(output_serializer.data, status=status.HTTP_201_CREATED)

    @extend_schema(responses=StudentSourceDetailSerializer)
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    @extend_schema(
        request=StudentSourceUpdateSerializer,
        responses=StudentSourceDetailSerializer,
    )
    def partial_update(self, request, *args, **kwargs):
        source = self.get_object()
        serializer = self.get_serializer(source, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        source = serializer.save()
        output_serializer = StudentSourceDetailSerializer(
            source,
            context=self.get_serializer_context(),
        )
        return Response(output_serializer.data)

    def perform_destroy(self, instance):
        file_field = instance.file
        instance.delete()
        if file_field:
            file_field.delete(save=False)

    @extend_schema(
        methods=['GET'],
        responses={(200, 'application/octet-stream'): bytes},
        description='Download this source through the authenticated ownership boundary.',
    )
    @action(detail=True, methods=['get'], url_path='download')
    def download(self, request, pk=None):
        source = self.get_object()
        if not source.file:
            raise Http404
        response = FileResponse(
            source.file.open('rb'),
            as_attachment=True,
            filename=source.original_filename or f'source-{source.pk}',
            content_type=source.mime_type or 'application/octet-stream',
        )
        response['Cache-Control'] = 'private, no-store'
        response['X-Content-Type-Options'] = 'nosniff'
        return response

    @extend_schema(
        methods=['POST'],
        responses=StudentSourceDetailSerializer,
        description='Queue source processing and return immediately.',
    )
    @action(detail=True, methods=['post'], url_path='process')
    def process(self, request, pk=None):
        source = self.get_object()
        if source.status == StudentSource.Status.PROCESSING:
            return Response(
                {'message': 'المصدر قيد المعالجة بالفعل.', 'source': StudentSourceDetailSerializer(source, context=self.get_serializer_context()).data},
                status=status.HTTP_202_ACCEPTED,
            )
        transaction.on_commit(lambda: process_source_task.delay(source.pk))
        serializer = StudentSourceDetailSerializer(source, context=self.get_serializer_context())
        return Response(
            {'message': 'تمت إضافة المصدر إلى طابور المعالجة.', 'source': serializer.data},
            status=status.HTTP_202_ACCEPTED,
        )

    @extend_schema(
        methods=['GET'],
        responses=dict,
        description='Return available Baraq character actions for this source.',
    )
    @action(detail=True, methods=['get'], url_path='capabilities')
    def capabilities(self, request, pk=None):
        source = self.get_object()
        return Response(get_source_character_capabilities(source, features=get_user_features(request.user)))

    @extend_schema(
        methods=['POST'],
        request=UseWithCharacterSerializer,
        responses=SourceCharacterResponseSerializer,
        description='Use the source with one Baraq character.',
    )
    @action(detail=True, methods=['post'], url_path='use-with-character')
    def use_with_character(self, request, pk=None):
        source = self.get_object()
        serializer = UseWithCharacterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = use_source_with_character(
            request.user,
            source,
            serializer.validated_data['character'],
            action=serializer.validated_data.get('action'),
        )
        return Response(self._build_character_response(result))

    @action(detail=True, methods=['post'], url_path='use-with-khota')
    def use_with_khota(self, request, pk=None):
        return self._use_with_fixed_character(StudentSourceInteraction.Character.KHOTA)

    @action(detail=True, methods=['post'], url_path='use-with-fahes')
    def use_with_fahes(self, request, pk=None):
        return self._use_with_fixed_character(StudentSourceInteraction.Character.FAHES)

    @action(detail=True, methods=['post'], url_path='use-with-rasheed')
    def use_with_rasheed(self, request, pk=None):
        return self._use_with_fixed_character(StudentSourceInteraction.Character.RASHEED)

    @action(detail=True, methods=['post'], url_path='use-with-kholasa')
    def use_with_kholasa(self, request, pk=None):
        return self._use_with_fixed_character(StudentSourceInteraction.Character.KHOLASA)

    @action(detail=True, methods=['post'], url_path='use-with-sada')
    def use_with_sada(self, request, pk=None):
        return self._use_with_fixed_character(StudentSourceInteraction.Character.SADA)

    def _use_with_fixed_character(self, character):
        source = self.get_object()
        result = use_source_with_character(self.request.user, source, character)
        return Response(self._build_character_response(result))

    def _character_is_available(self, source, character):
        capability = get_source_character_capabilities(source).get(character)
        return bool(capability and capability.get('available'))

    def _build_character_response(self, result):
        payload = {
            key: value
            for key, value in result.items()
            if key not in {'interaction', 'study_plan', 'quiz'}
        }

        ai_job = result.get('ai_job')
        if ai_job:
            payload['ai_job'] = AIJobSerializer(ai_job).data

        interaction = result.get('interaction')
        if interaction:
            payload['interaction'] = StudentSourceInteractionSerializer(interaction).data

        study_plan = result.get('study_plan')
        if study_plan:
            plan = get_user_study_plan_queryset(self.request.user).filter(id=study_plan.id).first()
            payload['study_plan'] = StudyPlanListSerializer(plan or study_plan).data

        quiz = result.get('quiz')
        if quiz:
            quiz = get_user_quiz_detail(self.request.user, quiz.id) or quiz
            payload['quiz'] = QuizListSerializer(quiz).data

        return payload


@extend_schema(tags=['Student Source Collections'])
class StudentSourceCollectionViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['name', 'description']
    ordering_fields = ['created_at', 'updated_at', 'name']
    ordering = ['-updated_at']
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_queryset(self):
        queryset = StudentSourceCollection.objects.select_related(
            'user',
            'subject',
            'subject__education_stage',
            'project',
        ).prefetch_related('sources').filter(user=self.request.user)

        status_value = self.request.query_params.get('status')
        if status_value:
            queryset = queryset.filter(status=status_value)

        subject = self.request.query_params.get('subject')
        if subject:
            queryset = queryset.filter(subject_id=subject)

        project = self.request.query_params.get('project')
        if project:
            queryset = queryset.filter(project__public_id=project)

        return queryset

    def get_serializer_class(self):
        if self.action == 'list':
            return StudentSourceCollectionListSerializer
        if self.action in {'create', 'partial_update'}:
            return StudentSourceCollectionCreateUpdateSerializer
        if self.action == 'use_with_character':
            return UseWithCharacterSerializer
        return StudentSourceCollectionDetailSerializer

    @extend_schema(
        parameters=[
            OpenApiParameter(name='status', type=str),
            OpenApiParameter(name='subject', type=int),
            OpenApiParameter(name='project', type=str),
        ],
        responses=StudentSourceCollectionListSerializer(many=True),
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(
        request=StudentSourceCollectionCreateUpdateSerializer,
        responses={201: StudentSourceCollectionDetailSerializer},
    )
    def create(self, request, *args, **kwargs):
        with transaction.atomic():
            locked_user = request.user.__class__.objects.select_for_update().get(pk=request.user.pk)
            can_create_collection(locked_user)
            serializer = self.get_serializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            collection = serializer.save()
            consume_collection_created(locked_user)
        output_serializer = StudentSourceCollectionDetailSerializer(
            collection,
            context=self.get_serializer_context(),
        )
        return Response(output_serializer.data, status=status.HTTP_201_CREATED)

    @extend_schema(
        request=StudentSourceCollectionCreateUpdateSerializer,
        responses=StudentSourceCollectionDetailSerializer,
    )
    def partial_update(self, request, *args, **kwargs):
        collection = self.get_object()
        serializer = self.get_serializer(collection, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        collection = serializer.save()
        output_serializer = StudentSourceCollectionDetailSerializer(
            collection,
            context=self.get_serializer_context(),
        )
        return Response(output_serializer.data)

    def destroy(self, request, *args, **kwargs):
        collection = self.get_object()
        if collection.sources.exists():
            raise ValidationError(
                {'collection': 'لا يمكن حذف مجلد يحتوي على مصادر. انقل المصادر أو احذفها أولًا.'}
            )
        return super().destroy(request, *args, **kwargs)

    @extend_schema(methods=['GET'], responses=StudentSourceBriefSerializer(many=True))
    @action(detail=True, methods=['get'], url_path='sources')
    def sources(self, request, pk=None):
        collection = self.get_object()
        sources = collection.sources.select_related('subject', 'collection').filter(
            user=request.user,
        )
        serializer = StudentSourceBriefSerializer(
            sources,
            many=True,
            context=self.get_serializer_context(),
        )
        return Response(serializer.data)

    @extend_schema(methods=['GET'], responses=dict)
    @action(detail=True, methods=['get'], url_path='capabilities')
    def capabilities(self, request, pk=None):
        collection = self.get_object()
        return Response(get_collection_character_capabilities(collection, features=get_user_features(request.user)))

    @extend_schema(
        methods=['POST'],
        request=UseWithCharacterSerializer,
        responses=SourceCharacterResponseSerializer,
    )
    @action(detail=True, methods=['post'], url_path='use-with-character')
    def use_with_character(self, request, pk=None):
        collection = self.get_object()
        serializer = UseWithCharacterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = use_collection_with_character(
            request.user,
            collection,
            serializer.validated_data['character'],
            action=serializer.validated_data.get('action'),
        )
        return Response(self._build_character_response(result))

    @action(detail=True, methods=['post'], url_path='use-with-khota')
    def use_with_khota(self, request, pk=None):
        return self._use_with_fixed_character(StudentSourceInteraction.Character.KHOTA)

    @action(detail=True, methods=['post'], url_path='use-with-fahes')
    def use_with_fahes(self, request, pk=None):
        return self._use_with_fixed_character(StudentSourceInteraction.Character.FAHES)

    @action(detail=True, methods=['post'], url_path='use-with-rasheed')
    def use_with_rasheed(self, request, pk=None):
        return self._use_with_fixed_character(StudentSourceInteraction.Character.RASHEED)

    @action(detail=True, methods=['post'], url_path='use-with-kholasa')
    def use_with_kholasa(self, request, pk=None):
        return self._use_with_fixed_character(StudentSourceInteraction.Character.KHOLASA)

    @action(detail=True, methods=['post'], url_path='use-with-sada')
    def use_with_sada(self, request, pk=None):
        return self._use_with_fixed_character(StudentSourceInteraction.Character.SADA)

    def _use_with_fixed_character(self, character):
        collection = self.get_object()
        result = use_collection_with_character(self.request.user, collection, character)
        return Response(self._build_character_response(result))

    def _character_is_available(self, collection, character):
        capability = get_collection_character_capabilities(collection).get(character)
        return bool(capability and capability.get('available'))

    def _build_character_response(self, result):
        payload = {
            key: value
            for key, value in result.items()
            if key not in {'interaction', 'study_plan', 'quiz'}
        }

        ai_job = result.get('ai_job')
        if ai_job:
            payload['ai_job'] = AIJobSerializer(ai_job).data

        interaction = result.get('interaction')
        if interaction:
            payload['interaction'] = StudentSourceInteractionSerializer(interaction).data

        study_plan = result.get('study_plan')
        if study_plan:
            plan = get_user_study_plan_queryset(self.request.user).filter(id=study_plan.id).first()
            payload['study_plan'] = StudyPlanListSerializer(plan or study_plan).data

        quiz = result.get('quiz')
        if quiz:
            quiz = get_user_quiz_detail(self.request.user, quiz.id) or quiz
            payload['quiz'] = QuizListSerializer(quiz).data

        return payload
