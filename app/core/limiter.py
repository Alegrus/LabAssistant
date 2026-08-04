"""Shared slowapi Limiter instance (N2 — app-level rate limiting)."""
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
