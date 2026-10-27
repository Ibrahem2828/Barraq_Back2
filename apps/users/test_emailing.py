"""What a mailbox provider can judge from the OTP and reset messages themselves.

These tests pin the properties that decide inbox versus spam and that code
controls: a real sender name, a Message-ID in Baraq's domain, a text part
matching the HTML part, no links in an OTP message, one honest link in a reset
message, and the automatic-mail headers. Authentication (SPF/DKIM/DMARC) is DNS
and provider configuration and is covered by docs/EMAIL_DELIVERABILITY.md.
"""

import email
import re
from email import policy
from html import unescape
from html.parser import HTMLParser
from unittest import mock

from django.core import mail
from django.test import SimpleTestCase, TestCase, override_settings

from .emailing import build_otp_email, build_password_reset_email
from .tasks import send_email_otp, send_password_reset_email

SETTINGS = {
    'DEFAULT_FROM_EMAIL': 'barraq.ai.team@gmail.com',
    'EMAIL_REPLY_TO': '',
    'EMAIL_MESSAGE_ID_DOMAIN': 'baraqapp.com',
    'EMAIL_BACKEND': 'django.core.mail.backends.locmem.EmailBackend',
}


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        if tag == 'a':
            self.links.append(dict(attrs).get('href'))


def parsed(message):
    """The message as a receiving server sees it: serialised, then parsed again."""
    return email.message_from_bytes(message.message().as_bytes(), policy=policy.default)


def parts(message):
    msg = parsed(message)
    text = msg.get_body(preferencelist=('plain',)).get_content()
    html = msg.get_body(preferencelist=('html',)).get_content()
    return msg, text, html


@override_settings(**SETTINGS)
class OtpMessageTests(SimpleTestCase):
    def build(self, code='482913'):
        return build_otp_email('student@example.com', code, 10)

    def test_the_code_is_in_the_subject_and_in_both_parts(self):
        msg, text, html = parts(self.build())

        self.assertEqual(str(msg['Subject']), 'رمز تحقق برّاق: 482913')
        self.assertIn('482913', text)
        self.assertIn('482913', html)

    def test_it_is_multipart_alternative_with_matching_text_and_html(self):
        msg, text, html = parts(self.build())

        self.assertEqual(msg.get_content_type(), 'multipart/alternative')
        self.assertEqual([p.get_content_type() for p in msg.iter_parts()], ['text/plain', 'text/html'])
        # The HTML part must not say anything the text part does not.
        words = lambda value: set(re.findall(r'[\w؀-ۿ]+', re.sub(r'<[^>]+>', ' ', value)))  # noqa: E731
        self.assertEqual(words(text) - words(html), set())

    def test_both_parts_are_utf8_and_carry_arabic_and_english(self):
        msg, text, _ = parts(self.build())

        for part in msg.iter_parts():
            self.assertEqual(part.get_content_charset(), 'utf-8')
        self.assertIn('لا تشاركه مع أحد', text)
        self.assertIn('Never share it', text)

    def test_the_sender_has_a_display_name_and_a_valid_address(self):
        msg, _, _ = parts(self.build())

        sender = msg['From']
        self.assertEqual(sender.addresses[0].addr_spec, 'barraq.ai.team@gmail.com')
        self.assertEqual(sender.addresses[0].display_name, 'برّاق')

    def test_a_configured_display_name_is_kept(self):
        with override_settings(DEFAULT_FROM_EMAIL='Baraq <no-reply@baraqapp.com>'):
            msg, _, _ = parts(self.build())
        self.assertEqual(msg['From'].addresses[0].display_name, 'Baraq')
        self.assertEqual(msg['From'].addresses[0].addr_spec, 'no-reply@baraqapp.com')

    def test_the_message_id_belongs_to_a_domain_baraq_owns(self):
        msg, _, _ = parts(self.build())

        self.assertTrue(re.fullmatch(r'<[\w.\-]+@baraqapp\.com>', str(msg['Message-ID'])), msg['Message-ID'])

    def test_each_message_gets_its_own_message_id(self):
        self.assertNotEqual(
            parsed(self.build())['Message-ID'], parsed(self.build())['Message-ID']
        )

    def test_it_is_marked_as_automatic_mail(self):
        msg, _, _ = parts(self.build())

        self.assertEqual(msg['Auto-Submitted'], 'auto-generated')
        self.assertEqual(msg['X-Auto-Response-Suppress'], 'All')
        self.assertEqual(msg['Content-Language'], 'ar')

    def test_it_has_no_reply_to_unless_one_is_configured(self):
        self.assertIsNone(parsed(self.build())['Reply-To'])
        with override_settings(EMAIL_REPLY_TO='support@baraqapp.com'):
            self.assertEqual(str(parsed(self.build())['Reply-To']), 'support@baraqapp.com')

    def test_an_otp_message_has_no_links_images_scripts_or_tracking(self):
        _, text, html = parts(self.build())
        collector = _Links()
        collector.feed(html)

        self.assertNotIn('http', text.lower())
        self.assertNotIn('http', html.lower())
        self.assertEqual(collector.links, [])
        for tag in ('img', 'script', 'iframe', 'link', 'style', 'form', 'video', 'object'):
            self.assertNotIn(tag, collector.tags)

    def test_it_is_short_and_not_shouty(self):
        _, text, html = parts(self.build())
        letters = re.findall(r'[A-Za-z]', text)

        self.assertLess(len(text), 700)
        self.assertLess(len(html), 4000)
        upper = sum(1 for ch in letters if ch.isupper())
        self.assertLess(upper / max(len(letters), 1), 0.15)
        self.assertNotRegex(text + html, r'!{2,}|\bFREE\b|\bwinner\b|\bclick here\b')

    def test_the_header_block_has_no_injection(self):
        msg, _, _ = parts(self.build())
        for name in ('Subject', 'From', 'To'):
            self.assertNotRegex(str(msg[name]), r'[\r\n]')

    def test_only_a_numeric_code_is_accepted(self):
        for bad in ('12', 'abcd12', '123456\nBcc: x@example.com', '', '1' * 11, '12 34 56'):
            with self.assertRaises(ValueError, msg=bad):
                self.build(bad)


@override_settings(**SETTINGS)
class ResetMessageTests(SimpleTestCase):
    URL = 'https://web.baraqapp.com/ar/reset-password?uid=Mg&token=cc3x9z-8f5d2a1b3c4e5f6a7b8c9d0e1f2a3b4c'

    def build(self):
        return build_password_reset_email('student@example.com', self.URL)

    def test_there_is_exactly_one_link_and_its_text_is_its_destination(self):
        _, text, html = parts(self.build())
        collector = _Links()
        collector.feed(html)

        self.assertEqual(collector.links, [self.URL])
        self.assertIn(self.URL, text)
        # What the link shows is where it goes (the phishing tell is the opposite).
        visible = re.search(r'<a [^>]*>(.*?)</a>', html, re.S).group(1)
        self.assertEqual(unescape(visible), self.URL)

    def test_it_carries_the_same_headers_as_the_otp_message(self):
        msg, _, _ = parts(self.build())

        self.assertEqual(msg['Auto-Submitted'], 'auto-generated')
        self.assertEqual(msg['From'].addresses[0].display_name, 'برّاق')
        self.assertTrue(str(msg['Message-ID']).endswith('@baraqapp.com>'))

    def test_a_non_http_link_is_refused(self):
        for bad in ('javascript:alert(1)', '//evil.example/x', 'ftp://x', ''):
            with self.assertRaises(ValueError, msg=bad):
                build_password_reset_email('student@example.com', bad)

    def test_html_in_the_link_cannot_break_out(self):
        _, _, html = parts(build_password_reset_email('s@example.com', 'https://x.example/?a=1"><script>alert(1)</script>'))
        self.assertNotIn('<script>', html)


@override_settings(**SETTINGS)
class DeliveryPathTests(TestCase):
    """The tasks the application actually runs, through a real mail backend."""

    def test_the_otp_task_sends_one_message_to_the_student(self):
        accepted = send_email_otp('student@example.com', '482913')

        self.assertEqual(accepted, 1)
        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(sent.to, ['student@example.com'])
        self.assertIn('482913', sent.subject)
        self.assertEqual(sent.alternatives[0][1], 'text/html')

    def test_the_reset_task_sends_one_message(self):
        send_password_reset_email('student@example.com', 'https://web.baraqapp.com/ar/reset-password?uid=1&token=t')
        self.assertEqual(len(mail.outbox), 1)

    def test_a_backend_that_accepts_nothing_is_a_failure_not_a_success(self):
        """Zero accepted used to look exactly like a delivered code."""
        with (
            mock.patch('django.core.mail.EmailMultiAlternatives.send', return_value=0),
            mock.patch.object(send_email_otp, 'retry', side_effect=RuntimeError('retried')) as retry,
            self.assertRaises(RuntimeError),
        ):
            send_email_otp('student@example.com', '482913')
        self.assertEqual(retry.call_count, 1)

    def test_nothing_identifying_reaches_the_log(self):
        with self.assertLogs('apps.users.tasks', level='INFO') as logs:
            send_email_otp('private.student@example.com', '482913')
        joined = ' '.join(logs.output) + ' ' + ' '.join(str(r.__dict__) for r in logs.records)
        self.assertNotIn('482913', joined)
        self.assertNotIn('private.student', joined)
        self.assertIn('otp_email_handed_to_smtp', joined)
