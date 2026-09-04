"""Structured preflight validation errors."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ValidationError:
    code: str
    message: str
    context: dict = field(default_factory=dict)
