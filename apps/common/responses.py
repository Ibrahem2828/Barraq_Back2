from rest_framework import status
from rest_framework.response import Response


def success_response(
    data=None,
    message='Success',
    status_code=status.HTTP_200_OK,
    meta=None,
):
    payload = {
        'success': True,
        'message': message,
    }
    if data is not None:
        payload['data'] = data
    if meta is not None:
        payload['meta'] = meta
    return Response(payload, status=status_code)


def error_response(
    message='Error',
    errors=None,
    status_code=status.HTTP_400_BAD_REQUEST,
    code=None,
):
    payload = {
        'success': False,
        'message': message,
    }
    if errors is not None:
        payload['errors'] = errors
    if code is not None:
        payload['code'] = code
    return Response(payload, status=status_code)


def validation_error_response(errors, message='Validation error'):
    return error_response(
        message=message,
        errors=errors,
        status_code=status.HTTP_400_BAD_REQUEST,
        code='validation_error',
    )
