"""Application error classes with HTTP status codes and error codes."""


class AppError(Exception):
    status: int = 500
    code: str = "INTERNAL"
    message: str = "Internal server error"

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class ValidationError(AppError):
    status: int = 400


class Unauthorized(AppError):
    status: int = 401


class Forbidden(AppError):
    status: int = 403


class NotFound(AppError):
    status: int = 404


class Conflict(AppError):
    status: int = 409


class Unavailable(AppError):
    status: int = 503


class InternalError(AppError):
    status: int = 500
