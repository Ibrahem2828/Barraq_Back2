from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from .models import Project, ProjectActivity


class ProjectApiTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="owner@example.com", full_name="Owner User", password="A-safe-password-123"
        )
        self.other = get_user_model().objects.create_user(
            email="other@example.com", full_name="Other User", password="A-safe-password-123"
        )
        self.client.force_authenticate(self.user)

    def test_owner_can_create_archive_and_soft_delete_project(self):
        response = self.client.post("/api/v1/projects/", {"title": "Biology"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        project = Project.objects.get(public_id=response.data["public_id"])
        self.assertEqual(project.owner_id, self.user.id)
        self.assertTrue(ProjectActivity.objects.filter(project=project, event_type="project.created").exists())

        response = self.client.post(f"/api/v1/projects/{project.public_id}/archive/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        project.refresh_from_db()
        self.assertEqual(project.status, Project.Status.ARCHIVED)

        response = self.client.delete(f"/api/v1/projects/{project.public_id}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        project.refresh_from_db()
        self.assertTrue(project.is_deleted)

    def test_project_is_invisible_to_other_user(self):
        project = Project.objects.create(owner=self.user, title="Private workspace")
        self.client.force_authenticate(self.other)
        response = self.client.get(f"/api/v1/projects/{project.public_id}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
