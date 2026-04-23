from __future__ import annotations

import httpx


def get(url: str, **kwargs):
    kwargs.setdefault("trust_env", False)
    return httpx.get(url, **kwargs)


def post(url: str, **kwargs):
    kwargs.setdefault("trust_env", False)
    return httpx.post(url, **kwargs)


def put(url: str, **kwargs):
    kwargs.setdefault("trust_env", False)
    return httpx.put(url, **kwargs)


def delete(url: str, **kwargs):
    kwargs.setdefault("trust_env", False)
    return httpx.delete(url, **kwargs)
