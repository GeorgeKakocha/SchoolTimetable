"""Top-level school identity."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class School:
    id: str
    name: str
