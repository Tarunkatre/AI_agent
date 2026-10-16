from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    period: str = "2024-Q4"
    functional_currency: str = "USD"
    fuzzy_floor: float = 0.6
    fuzzy_accept: float = 0.9
    llm_self_min: float = 0.8
    llm_floor_min: float = 0.6
    project_root: Path = field(default_factory=lambda: Path(__file__).resolve().parent.parent)

    def gates(self) -> dict[str, float]:
        return {
            "fuzzy_floor": self.fuzzy_floor,
            "fuzzy_accept": self.fuzzy_accept,
            "llm_self_min": self.llm_self_min,
            "llm_floor_min": self.llm_floor_min,
        }
