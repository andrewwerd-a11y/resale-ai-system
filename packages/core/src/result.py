from __future__ import annotations
from typing import Generic, TypeVar, Optional

T = TypeVar("T")


class Result(Generic[T]):
    """Monadic result type — never raises from business logic."""

    def __init__(self, value: Optional[T], error: Optional[str], ok: bool) -> None:
        self._value = value
        self._error = error
        self._ok = ok

    @classmethod
    def success(cls, value: T) -> "Result[T]":
        return cls(value=value, error=None, ok=True)

    @classmethod
    def failure(cls, error: str) -> "Result[T]":
        return cls(value=None, error=error, ok=False)

    @property
    def is_ok(self) -> bool:
        return self._ok

    @property
    def is_err(self) -> bool:
        return not self._ok

    @property
    def value(self) -> T:
        if not self._ok:
            raise RuntimeError(f"Called .value on failure: {self._error}")
        return self._value  # type: ignore

    @property
    def error(self) -> str:
        if self._ok:
            raise RuntimeError("Called .error on success")
        return self._error  # type: ignore

    def unwrap_or(self, default: T) -> T:
        return self._value if self._ok else default  # type: ignore

    def __repr__(self) -> str:
        if self._ok:
            return f"Result.success({self._value!r})"
        return f"Result.failure({self._error!r})"
