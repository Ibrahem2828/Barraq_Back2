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
        # Read by apps.common.exceptions.domain_error_code to put a stable,
        # branchable code at the top level of the error envelope. Declared as
        # an attribute rather than left only inside the payload so it can
        # never be confused with a serializer field that happens to be named
        # "code".
        self.domain_code = code or self.default_code
        super().__init__(payload, code=code or self.default_code)


class SubscriptionLimitExceeded(SubscriptionError):
    status_code = 403
    default_code = 'subscription_limit_exceeded'


class SubscriptionFeatureNotAllowed(SubscriptionError):
    status_code = 403
    default_code = 'subscription_feature_not_allowed'
