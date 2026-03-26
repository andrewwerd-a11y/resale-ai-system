"""
Result[T] — a typed success/failure container.

Keeps error handling explicit throughout the pipeline without
scattering try/except everywhere or hiding failures silently.

Usage:
    result = do_something()
    if result.ok:
        use(result.value)
    else:
        log(result.error)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass
class Result(Generic[T]):
    ok: bool
    value: T | None = None
    error: str | None = None
    error_code: str | None = None
    details: dict = field(default_factory=dict)

    @classmethod
    def success(cls, value: T, **details) -> "Result[T]":
        return cls(ok=True, value=value, details=details)

    @classmethod
    def failure(cls, error: str, error_code: str | None = None, **details) -> "Result[T]":
        return cls(ok=False, error=error, error_code=error_code, details=details)

    def unwrap(self) -> T:
        """Return value or raise RuntimeError."""
        if not self.ok:
            raise RuntimeError(f"Result.unwrap() on failure: {self.error}")
        return self.value  # type: ignore[return-value]

    def __bool__(self) -> bool:
        return self.ok
