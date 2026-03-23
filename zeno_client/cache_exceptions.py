from __future__ import annotations


class CacheError(Exception):
    pass


class CacheLockTimeoutError(CacheError):
    pass


class CacheCorruptError(CacheError):
    def __init__(self, message: str, *, content_id: str):
        super().__init__(message)
        self.content_id = content_id

