import io

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from openpyxl import load_workbook
from rest_framework import status
from rest_framework.test import APITestCase

from .admin import WaitlistEntryAdmin, export_waitlist_as_excel
from .models import WaitlistEntry

User = get_user_model()


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1'])
class WaitlistApiTests(APITestCase):
    def tearDown(self):
        # The join endpoint is scoped-throttled (anti-spam); clear it between
        # tests so one test's requests don't count against the next test's.
        cache.clear()

    def test_joining_creates_an_entry_and_returns_the_new_count(self):
        response = self.client.post(
            reverse('waitlist-join'),
            {'email': 'Student@Example.com', 'full_name': 'Sara Ahmad', 'locale': 'ar'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data['created'])
        self.assertEqual(response.data['count'], 1)
        # Emails are normalized (trimmed + lowercased) before storage.
        entry = WaitlistEntry.objects.get(email='student@example.com')
        self.assertEqual(entry.full_name, 'Sara Ahmad')

    def test_a_name_is_required_to_join(self):
        response = self.client.post(reverse('waitlist-join'), {'email': 'noname@example.com'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(WaitlistEntry.objects.count(), 0)

    def test_joining_twice_with_the_same_email_is_not_an_error(self):
        self.client.post(reverse('waitlist-join'), {'email': 'student@example.com', 'full_name': 'Sara'}, format='json')
        response = self.client.post(
            reverse('waitlist-join'), {'email': 'student@example.com', 'full_name': 'Sara'}, format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data['created'])
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(WaitlistEntry.objects.count(), 1)

    def test_rejoining_with_a_corrected_name_updates_the_existing_entry(self):
        self.client.post(reverse('waitlist-join'), {'email': 'student@example.com', 'full_name': 'Sarra'}, format='json')
        self.client.post(reverse('waitlist-join'), {'email': 'student@example.com', 'full_name': 'Sara'}, format='json')

        self.assertEqual(WaitlistEntry.objects.count(), 1)
        self.assertEqual(WaitlistEntry.objects.get().full_name, 'Sara')

    def test_invalid_email_is_rejected(self):
        response = self.client.post(
            reverse('waitlist-join'), {'email': 'not-an-email', 'full_name': 'Sara'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(WaitlistEntry.objects.count(), 0)

    def test_filled_honeypot_field_is_rejected_as_spam(self):
        response = self.client.post(
            reverse('waitlist-join'),
            {'email': 'bot@example.com', 'full_name': 'Bot', 'company': 'not actually blank'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(WaitlistEntry.objects.count(), 0)

    def test_no_authentication_is_required_to_join(self):
        response = self.client.post(
            reverse('waitlist-join'), {'email': 'anon@example.com', 'full_name': 'Anon'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_count_endpoint_reflects_real_entries_and_needs_no_auth(self):
        WaitlistEntry.objects.create(email='a@example.com', full_name='A')
        WaitlistEntry.objects.create(email='b@example.com', full_name='B')

        response = self.client.get(reverse('waitlist-count'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 2)


class WaitlistExcelExportTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.admin_user = User.objects.create_superuser(
            email='root@example.com', password='StrongPass123!', full_name='Root',
        )
        self.entry = WaitlistEntry.objects.create(
            email='student@example.com', full_name='Sara Ahmad', locale=WaitlistEntry.Locale.AR,
        )

    def test_export_action_returns_a_readable_xlsx_with_the_right_rows(self):
        request = self.factory.post('/admin/waitlist/waitlistentry/')
        request.user = self.admin_user
        response = export_waitlist_as_excel(WaitlistEntryAdmin(WaitlistEntry, AdminSite()), request, WaitlistEntry.objects.all())

        self.assertEqual(
            response['Content-Type'],
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        self.assertIn('attachment; filename=', response['Content-Disposition'])

        workbook = load_workbook(filename=io.BytesIO(response.content))
        sheet = workbook.active
        header = [cell.value for cell in sheet[1]]
        self.assertEqual(header[0], 'الاسم / Name')
        data_row = [cell.value for cell in sheet[2]]
        self.assertEqual(data_row[0], 'Sara Ahmad')
        self.assertEqual(data_row[1], 'student@example.com')
