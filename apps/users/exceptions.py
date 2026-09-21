"""Stable, safe domain errors for registration and email verification."""

from rest_framework.exceptions import APIException, ValidationError


class RegistrationValidationError(ValidationError):
    """A validation error whose stable domain code reaches API consumers."""

    def __init__(self, detail, *, code: str):
        self.domain_code = code
        super().__init__(detail)


class RegistrationDeliveryUnavailable(APIException):
    status_code = 503
    default_detail = 'Email delivery is temporarily unavailable.'
    default_code = 'email_delivery_unavailable'

    def __init__(self):
        self.domain_code = self.default_code
        super().__init__({'detail': self.default_detail}, code=self.default_code)


class RegistrationConflict(APIException):
    status_code = 409
    default_detail = 'Registration could not be completed safely. Please try again.'
    default_code = 'registration_conflict'

    def __init__(self):
        self.domain_code = self.default_code
        super().__init__({'detail': self.default_detail}, code=self.default_code)
