from collections.abc import Sequence

from drf_spectacular.utils import extend_schema
from rest_framework import generics, permissions, status
from rest_framework.permissions import BasePermission
from rest_framework.response import Response

from apps.projects.stage_projects import ensure_stage_projects

from .models import StudentProfile
from .serializers import StudentProfileSerializer, StudentProfileSetupSerializer


class StudentProfileObjectMixin:
    permission_classes: Sequence[type[BasePermission]] = [permissions.IsAuthenticated]

    def get_object(self):
        profile, _ = StudentProfile.objects.get_or_create(user=self.request.user)
        return profile


@extend_schema(tags=['Students'])
class StudentProfileSetupView(generics.CreateAPIView):
    serializer_class = StudentProfileSetupSerializer
    permission_classes = [permissions.IsAuthenticated]

    def create(self, request, *args, **kwargs):
        profile, created = StudentProfile.objects.get_or_create(user=request.user)
        serializer = self.get_serializer(profile, data=request.data)
        serializer.is_valid(raise_exception=True)
        profile = serializer.save(is_setup_completed=True)
        # One ready project per subject of the chosen stage (e.g. بكالوريا).
        ensure_stage_projects(request.user)
        response_serializer = StudentProfileSerializer(profile)
        status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        return Response(response_serializer.data, status=status_code)


@extend_schema(tags=['Students'])
class StudentProfileDetailView(StudentProfileObjectMixin, generics.RetrieveUpdateAPIView):  # type: ignore[misc]
    serializer_class = StudentProfileSerializer
    http_method_names = ['get', 'patch', 'head', 'options']

    def perform_update(self, serializer):
        serializer.save()
        # Changing the stage gives the new stage's projects; existing ones stay.
        ensure_stage_projects(self.request.user)
