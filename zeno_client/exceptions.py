from __future__ import annotations


class ZenoAPIError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None, detail: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail


class ServiceUnavailable(ZenoAPIError):
    pass


class BadRequest(ZenoAPIError):
    pass


class NotFound(ZenoAPIError):
    pass


class Conflict(ZenoAPIError):
    pass


class Forbidden(ZenoAPIError):
    pass


class ResolveBadRequest(BadRequest):
    pass


class ResolveNotFound(NotFound):
    pass


class InvalidHash(BadRequest):
    pass


class ContentHashMismatch(BadRequest):
    pass


class BlobNotFound(NotFound):
    pass


class RegisterVersionConflict(Conflict):
    pass


class RegisterContentNotFound(Conflict):
    pass


class LockHeldByOther(Conflict):
    pass


class LockNotOwned(Forbidden):
    pass


class LockNotFound(NotFound):
    pass

