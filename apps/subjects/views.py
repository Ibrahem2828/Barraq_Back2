from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics, permissions

from apps.common.pagination import StandardResultsSetPagination

from .models import EducationStage, Subject, UserSubject
from .serializers import (
    EducationStageSerializer,
    SubjectSerializer,
    UserSubjectReadSerializer,
    UserSubjectWriteSerializer,
)


def _parse_bool_query_param(value):
    if value is None:
        return None

    normalized = value.strip().lower()
    if normalized in {'1', 'true', 'yes'}:
        return True
    if normalized in {'0', 'false', 'no'}:
        return False
    return None


@extend_schema(tags=['Subjects'])
class EducationStageListView(generics.ListAPIView):
    serializer_class = EducationStageSerializer
    permission_classes = [permissions.AllowAny]
    pagination_class = None
    queryset = EducationStage.objects.filter(is_active=True).order_by('order', 'id')


@extend_schema(
    tags=['Subjects'],
    parameters=[
        OpenApiParameter(name='education_stage', type=int),
        OpenApiParameter(name='grade_level', type=str),
        OpenApiParameter(name='is_active', type=bool),
    ],
)
class SubjectListView(generics.ListAPIView):
    serializer_class = SubjectSerializer
    permission_classes = [permissions.AllowAny]
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        queryset = Subject.objects.select_related('education_stage').filter(
            education_stage__is_active=True,
        )

        education_stage = self.request.query_params.get('education_stage')
        if education_stage:
            queryset = queryset.filter(education_stage_id=education_stage)

        grade_level = self.request.query_params.get('grade_level')
        if grade_level:
            queryset = queryset.filter(grade_level__iexact=grade_level)

        is_active = _parse_bool_query_param(self.request.query_params.get('is_active'))
        queryset = queryset.filter(is_active=True) if is_active is None else queryset.filter(is_active=is_active)

        return queryset.order_by('education_stage__order', 'name')


@extend_schema(tags=['Subjects'])
class UserSubjectListCreateView(generics.ListCreateAPIView):
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        return UserSubject.objects.select_related(
            'subject',
            'subject__education_stage',
        ).filter(user=self.request.user)

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return UserSubjectWriteSerializer
        return UserSubjectReadSerializer


@extend_schema(tags=['Subjects'])
class UserSubjectDestroyView(generics.DestroyAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = UserSubjectReadSerializer

    def get_queryset(self):
        return UserSubject.objects.filter(user=self.request.user)
