"""Canonical identity normalisation shared by every authentication path."""

from django.contrib.auth.base_user import BaseUserManager


def normalize_email(value: str) -> str:
    """Return Baraq's canonical email identity.

    Django's built-in normaliser lower-cases the domain.  Baraq has always
    stored its user email identities lower-case as well, and PostgreSQL has a
    matching ``Lower(email)`` uniqueness constraint.  Keeping that final
    lower-case conversion in one place prevents registration, OTP and login
    from disagreeing about which account an address denotes.
    """

    return BaseUserManager.normalize_email(value or "").strip().lower()
