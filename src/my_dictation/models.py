from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Change:
    before: str
    after: str
    rule: str


@dataclass
class StageResult:
    name: str
    processor: str
    input: str
    output: str
    changes: list[Change] = field(default_factory=list)
    protected_terms: list[str] = field(default_factory=list)
    model: str | None = None
    accepted: bool | None = None
    error: str | None = None
    candidate_output: str | None = None
    rejected_output: str | None = None
    rejection_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}
