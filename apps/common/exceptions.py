from collections.abc import Mapping

from django.conf import settings
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler

DEFAULT_ERROR_MESSAGES = {
    status.HTTP_400_BAD_REQUEST: 'Validation error',
    status.HTTP_401_UNAUTHORIZED: 'Authentication failed',
    status.HTTP_403_FORBIDDEN: 'You do not have permission to perform this action.',
    status.HTTP_404_NOT_FOUND: 'Resource not found',
    status.HTTP_500_INTERNAL_SERVER_ERROR: 'Internal server error',
}

DEFAULT_ERROR_CODES = {
    status.HTTP_400_BAD_REQUEST: 'validation_error',
    status.HTTP_401_UNAUTHORIZED: 'authentication_error',
    status.HTTP_403_FORBIDDEN: 'permission_denied',
    status.HTTP_404_NOT_FOUND: 'not_found',
    status.HTTP_409_CONFLICT: 'conflict',
    status.HTTP_413_REQUEST_ENTITY_TOO_LARGE: 'payload_too_large',
    status.HTTP_429_TOO_MANY_REQUESTS: 'rate_limited',
    status.HTTP_500_INTERNAL_SERVER_ERROR: 'server_error',
}


def domain_error_code(exc):
    """The stable domain code an exception explicitly declares, if any.

    Read from a dedicated ``domain_code`` attribute the raising class sets --
    never from the response payload. A payload lookup would be
    user-influenced: ``SubscriptionPlan`` has a field literally named
    ``code``, and DRF leaves ``ValidationError({'code': 'text'})`` as a bare
    string, so request input could have been promoted into the contract.

    Until this was promoted, the envelope's top-level ``code`` was always the
    status-derived value -- ``permission_denied`` for every 403 -- while the
    real code stayed buried under ``errors``. Clients read the top-level
    field, so every subscription/file-limit mapping they had was dead code
    and predictable failures surfaced as a generic "forbidden".

    Exceptions that declare nothing keep their status-derived code, so
    standard DRF errors are unchanged for existing clients.
    """

    code = getattr(exc, 'domain_code', None)
    return code if isinstance(code, str) and code else None


def _build_error_payload(message, errors=None, code=None, extra=None, request_id=None):
    """Build the error envelope.

    ``extra`` is merged first so the envelope's own keys can never be
    shadowed by response data. A validation error on a field named ``code``
    -- SubscriptionPlan has one -- previously overwrote the envelope's
    ``code`` with the field's message, handing clients request input where
    they expect a stable contract value.
    """

    payload = dict(extra) if extra else {}
    payload.update(
        {
            'success': False,
            'message': message,
            'errors': errors if errors is not None else {},
            'code': code,
        }
    )
    if request_id:
        payload['request_id'] = request_id
    return payload


def custom_exception_handler(exc, context):
    response = exception_handler(exc, context)
    request_id = getattr(context.get('request'), 'request_id', None)

    if response is None:
        errors = {}
        if settings.DEBUG:
            errors['detail'] = str(exc)
        return Response(
            _build_error_payload(
                message=DEFAULT_ERROR_MESSAGES[status.HTTP_500_INTERNAL_SERVER_ERROR],
                errors=errors,
                code=DEFAULT_ERROR_CODES[status.HTTP_500_INTERNAL_SERVER_ERROR],
                request_id=request_id,
            ),
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    status_code = response.status_code
    data = response.data
    code = domain_error_code(exc) or DEFAULT_ERROR_CODES.get(status_code, 'request_error')

    if isinstance(data, Mapping):
        detail = data.get('detail')
        is_validation_error = status_code == status.HTTP_400_BAD_REQUEST and (
            'detail' not in data or len(data) > 1
        )

        if is_validation_error:
            payload = _build_error_payload(
                message=DEFAULT_ERROR_MESSAGES[status.HTTP_400_BAD_REQUEST],
                errors=data,
                code=code,
                request_id=request_id,
                extra=dict(data),
            )
        else:
            message = str(detail or DEFAULT_ERROR_MESSAGES.get(status_code, 'Request failed'))
            payload = _build_error_payload(
                message=message,
                errors=data,
                code=code,
                request_id=request_id,
            )
            if detail is not None:
                payload['detail'] = detail
    elif isinstance(data, list):
        payload = _build_error_payload(
            message=DEFAULT_ERROR_MESSAGES.get(status_code, 'Request failed'),
            errors=data,
            code=code,
            request_id=request_id,
            extra={'detail': data},
        )
    else:
        payload = _build_error_payload(
            message=str(data),
            errors={'detail': data},
            code=code,
            request_id=request_id,
            extra={'detail': data},
        )

    response.data = payload
    return response
