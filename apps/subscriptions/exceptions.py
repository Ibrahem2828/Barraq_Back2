from rest_framework.exceptions import APIException


class SubscriptionError(APIException):
    status_code = 400
    default_detail = 'Subscription error.'
    default_code = 'subscription_error'

    def __init__(self, detail=None, code=None, status_code=None, **extra):
        if status_code is not None:
            self.status_code = status_code
        payload = detail
        if isinstance(detail, str):
            payload = {'detail': detail}
        if payload is None:
            payload = {'detail': self.default_detail}
        if code:
            payload['code'] = code
        payload.update(extra)
        super().__init__(payload, code=code or self.default_code)


class SubscriptionLimitExceeded(SubscriptionError):
    status_code = 403
    default_code = 'subscription_limit_exceeded'


class SubscriptionFeatureNotAllowed(SubscriptionError):
    status_code = 403
    default_code = 'subscription_feature_not_allowed'
