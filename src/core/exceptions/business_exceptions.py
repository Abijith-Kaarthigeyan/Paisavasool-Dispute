from src.core.exceptions.base import AppException


class BusinessException(AppException):
    """Base exception for all business rules violations."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message, status_code=status_code)


class ValidationException(BusinessException):
    def __init__(self, message: str = "Validation failed"):
        super().__init__(message, status_code=400)


class CaseNotFoundException(BusinessException):
    def __init__(self, message: str = "Case not found"):
        super().__init__(message, status_code=404)


class ReviewQueueItemNotFoundException(BusinessException):
    def __init__(self, message: str = "Review queue item not found"):
        super().__init__(message, status_code=404)


class DisputeNotFoundException(BusinessException):
    def __init__(self, message: str = "Dispute not found"):
        super().__init__(message, status_code=404)


class SLAException(BusinessException):
    def __init__(self, message: str = "SLA configuration or runtime error"):
        super().__init__(message, status_code=400)


class AssignmentException(BusinessException):
    def __init__(self, message: str = "Assignment failed"):
        super().__init__(message, status_code=400)


class EscalationException(BusinessException):
    def __init__(self, message: str = "Escalation failed"):
        super().__init__(message, status_code=400)


class WorkflowException(BusinessException):
    def __init__(self, message: str = "Workflow context error"):
        super().__init__(message, status_code=400)


class WorkflowContextNotFoundException(BusinessException):
    def __init__(self, message: str = "Workflow context not found"):
        super().__init__(message, status_code=404)


class SLADetailsNotFoundException(BusinessException):
    def __init__(self, message: str = "SLA details not found"):
        super().__init__(message, status_code=404)


class ARServiceClientException(AppException):
    def __init__(
        self, message: str = "AR Service client error", status_code: int = 502
    ):
        super().__init__(message, status_code=status_code)


class UnauthorizedException(AppException):
    def __init__(self, message: str = "Unauthorized"):
        super().__init__(message, status_code=401)


class ForbiddenException(AppException):
    def __init__(self, message: str = "Forbidden"):
        super().__init__(message, status_code=403)
