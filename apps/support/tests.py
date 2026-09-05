from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from .models import SupportTicket

User = get_user_model()


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1'])
class SupportTicketApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='support-user@example.com', password='StrongPass123', full_name='Support User',
        )
        self.other_user = User.objects.create_user(
            email='other-support-user@example.com', password='StrongPass123', full_name='Other Support User',
        )

    def test_unauthenticated_request_is_rejected(self):
        response = self.client.get(reverse('support-ticket-list'))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_create_ticket_also_creates_the_first_message(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post(
            reverse('support-ticket-list'),
            {
                'subject': 'Cannot upload a source',
                'category': SupportTicket.Category.OTHER,
                'priority': 'medium',
                'message': 'The upload button does nothing when I tap it.',
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        ticket = SupportTicket.objects.get(user=self.user)
        self.assertEqual(ticket.messages.count(), 1)
        self.assertEqual(ticket.status, SupportTicket.Status.OPEN)

    def test_user_only_sees_their_own_tickets(self):
        SupportTicket.objects.create(user=self.user, subject='Mine', category=SupportTicket.Category.OTHER)
        SupportTicket.objects.create(user=self.other_user, subject='Not mine', category=SupportTicket.Category.OTHER)

        self.client.force_authenticate(user=self.user)
        response = self.client.get(reverse('support-ticket-list'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        subjects = {item['subject'] for item in response.data['results']}
        self.assertEqual(subjects, {'Mine'})

    def test_cannot_add_a_message_to_a_closed_ticket(self):
        ticket = SupportTicket.objects.create(
            user=self.user, subject='Closed already', category=SupportTicket.Category.OTHER,
            status=SupportTicket.Status.CLOSED,
        )
        self.client.force_authenticate(user=self.user)
        response = self.client.post(
            reverse('support-ticket-add-message', args=[ticket.id]),
            {'body': 'Still broken'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_close_action_sets_status_and_resolved_at(self):
        ticket = SupportTicket.objects.create(user=self.user, subject='Ready to close', category=SupportTicket.Category.OTHER)
        self.client.force_authenticate(user=self.user)
        response = self.client.post(reverse('support-ticket-close', args=[ticket.id]))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, SupportTicket.Status.CLOSED)
        self.assertIsNotNone(ticket.resolved_at)
