"""Transactional emails that have to reach the inbox, not the spam folder.

A verification code that lands in spam is, for the student, a code that never
arrived. What a mailbox provider can judge from the message itself, and what
this module therefore controls:

* a real display name on a valid From address, and a Message-ID in a domain
  Baraq owns (not the container's hostname);
* multipart/alternative with a text part that says the same as the HTML part
  (HTML-only or text-less mail is a classic spam signal);
* no links, images or tracking in an OTP message, and in a reset message exactly
  one link whose visible text is its destination (a link that shows one address
  and goes to another is what phishing looks like);
* the headers that mark mail as automatic, so out-of-office replies and mailing
  list software do not answer it;
* plain, short wording in the language the student reads, with the English
  equivalent below it.

Authentication (SPF, DKIM, DMARC for the sending domain) is DNS and provider
configuration, not code: see docs/EMAIL_DELIVERABILITY.md.
"""

from __future__ import annotations

import re
from email.utils import formataddr, make_msgid, parseaddr

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.utils.html import escape

BRAND_AR = 'برّاق'
_CODE_PATTERN = re.compile(r'^\d{4,10}$')

_FONT = "-apple-system,'Segoe UI',Tahoma,Arial,sans-serif"


def _sender() -> str:
    """The configured From, with a display name even when only an address is set."""
    name, address = parseaddr(settings.DEFAULT_FROM_EMAIL)
    return formataddr((name or BRAND_AR, address))


def _headers() -> dict[str, str]:
    headers = {
        # Marks the message as machine-generated: auto-responders must not reply to it.
        'Auto-Submitted': 'auto-generated',
        'X-Auto-Response-Suppress': 'All',
        'Content-Language': 'ar',
        'Message-ID': make_msgid(domain=getattr(settings, 'EMAIL_MESSAGE_ID_DOMAIN', 'baraqapp.com')),
    }
    return headers


def _compose(subject: str, text: str, html: str, recipient: str) -> EmailMultiAlternatives:
    reply_to = getattr(settings, 'EMAIL_REPLY_TO', '')
    message = EmailMultiAlternatives(
        subject=subject,
        body=text,
        from_email=_sender(),
        to=[recipient],
        reply_to=[reply_to] if reply_to else None,
        headers=_headers(),
    )
    message.attach_alternative(html, 'text/html')
    return message


def _page(inner: str) -> str:
    return (
        '<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{BRAND_AR}</title></head>'
        f'<body style="margin:0;padding:24px;background:#f5f5f7;font-family:{_FONT};color:#1d1d1f">'
        '<div style="max-width:480px;margin:0 auto;background:#ffffff;border-radius:12px;padding:28px">'
        f'<p style="margin:0 0 16px;font-size:20px;font-weight:700">{BRAND_AR}</p>{inner}</div></body></html>'
    )


def build_otp_email(recipient: str, code: str, ttl_minutes: int) -> EmailMultiAlternatives:
    """The registration / verification code message. No link, image or tracking."""
    if not _CODE_PATTERN.match(code):
        raise ValueError('An OTP must be 4-10 digits.')
    subject = f'رمز تحقق برّاق: {code}'
    text = (
        f'رمز التحقق الخاص بك في برّاق: {code}\n'
        f'صالح لمدة {ttl_minutes} دقائق. لا تشاركه مع أحد، فريق برّاق لن يطلبه منك أبدًا.\n'
        'إذا لم تطلب هذا الرمز فتجاهل هذه الرسالة.\n'
        '\n'
        f'Your Baraq verification code: {code}\n'
        f'It expires in {ttl_minutes} minutes. Never share it; Baraq staff will never ask for it.\n'
        'If you did not request this code, ignore this message.\n'
    )
    safe_code = escape(code)
    html = _page(
        '<p style="margin:0 0 8px;font-size:16px">رمز التحقق الخاص بك في برّاق</p>'
        '<p style="margin:0 0 16px;font-size:34px;font-weight:700;letter-spacing:8px;direction:ltr;'
        f'unicode-bidi:plaintext;text-align:center;background:#f5f5f7;border-radius:8px;padding:12px">{safe_code}</p>'
        f'<p style="margin:0 0 8px;font-size:14px;color:#515154">صالح لمدة {ttl_minutes} دقائق. '
        'لا تشاركه مع أحد، فريق برّاق لن يطلبه منك أبدًا.</p>'
        '<p style="margin:0 0 20px;font-size:14px;color:#515154">إذا لم تطلب هذا الرمز فتجاهل هذه الرسالة.</p>'
        '<hr style="border:0;border-top:1px solid #e5e5ea;margin:0 0 16px">'
        '<div dir="ltr" style="font-size:13px;color:#6e6e73;text-align:left">'
        f'<p style="margin:0 0 4px">Your Baraq verification code: <strong>{safe_code}</strong></p>'
        f'<p style="margin:0">It expires in {ttl_minutes} minutes. Never share it; Baraq staff will never ask for it. '
        'If you did not request this code, ignore this message.</p></div>'
    )
    return _compose(subject, text, html, recipient)


def build_password_reset_email(recipient: str, reset_url: str) -> EmailMultiAlternatives:
    """The reset message: exactly one link, and the text shows where it goes."""
    if not reset_url.startswith(('https://', 'http://')):
        raise ValueError('The reset link must be an absolute http(s) URL.')
    safe_url = escape(reset_url)
    text = (
        f'استخدم الرابط التالي لإعادة تعيين كلمة المرور:\n{reset_url}\n'
        'الرابط صالح لفترة محدودة. إذا لم تطلب ذلك فتجاهل هذه الرسالة وستبقى كلمة مرورك كما هي.\n'
        '\n'
        f'Use this link to reset your Baraq password:\n{reset_url}\n'
        'The link is valid for a limited time. If you did not ask for this, ignore this message; '
        'your password stays the same.\n'
    )
    html = _page(
        '<p style="margin:0 0 12px;font-size:16px">استخدم الرابط التالي لإعادة تعيين كلمة المرور:</p>'
        f'<p style="margin:0 0 16px;font-size:14px;direction:ltr;text-align:left;word-break:break-all">'
        f'<a href="{safe_url}">{safe_url}</a></p>'
        '<p style="margin:0 0 16px;font-size:14px;color:#515154">الرابط صالح لفترة محدودة. إذا لم تطلب ذلك '
        'فتجاهل هذه الرسالة وستبقى كلمة مرورك كما هي.</p>'
        '<hr style="border:0;border-top:1px solid #e5e5ea;margin:0 0 16px">'
        '<p dir="ltr" style="margin:0;font-size:13px;color:#6e6e73;text-align:left">Use the link above to reset your '
        'Baraq password. If you did not ask for this, ignore this message; your password stays the same.</p>'
    )
    return _compose('إعادة تعيين كلمة مرور برّاق', text, html, recipient)
