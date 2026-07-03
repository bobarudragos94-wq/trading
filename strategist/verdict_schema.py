"""Pydantic model + bounds validation for the LLM verdict (S10, §6.4).

Validation here is the FIRST line of defense (shape and types). The verdict
then still passes through ``riskguard.clamp_verdict`` which re-clamps every
field against the locked limits — validation failure at either stage can
only ever result in the defensive fallback, never a crash.
"""

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Verdict(BaseModel):
    model_config = ConfigDict(extra="ignore")

    regime: Literal["trend", "range", "high_volatility", "risk_off"]
    risk_mode: Literal["defensive", "neutral", "aggressive"]
    active_strategy: Literal["TrendRider", "MeanRevRanger", "flat"]
    pair_whitelist: list[str] = Field(min_length=1, max_length=30)
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = ""

    @field_validator("rationale", mode="before")
    @classmethod
    def _truncate_rationale(cls, v):  # max 600 chars, never a reason to fail
        return str(v)[:600] if v is not None else ""

    @field_validator("pair_whitelist", mode="before")
    @classmethod
    def _pairs_are_strings(cls, v):
        if isinstance(v, list):
            return [p for p in v if isinstance(p, str)]
        return v


# JSON schema sent to the API as output_config.format — guarantees the model
# response is machine-parseable JSON of this exact shape.
VERDICT_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "regime": {
            "type": "string",
            "enum": ["trend", "range", "high_volatility", "risk_off"],
        },
        "risk_mode": {
            "type": "string",
            "enum": ["defensive", "neutral", "aggressive"],
        },
        "active_strategy": {
            "type": "string",
            "enum": ["TrendRider", "MeanRevRanger", "flat"],
        },
        "pair_whitelist": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number"},
        "rationale": {"type": "string"},
    },
    "required": [
        "regime",
        "risk_mode",
        "active_strategy",
        "pair_whitelist",
        "confidence",
        "rationale",
    ],
    "additionalProperties": False,
}

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def parse_verdict(raw_text: str) -> tuple[Verdict | None, str | None]:
    """Parse and validate LLM output. Returns (verdict, error).

    Never raises. Tolerates markdown fences / prose around the JSON object
    (defense in depth — structured outputs should already prevent that).
    """
    if not isinstance(raw_text, str) or not raw_text.strip():
        return None, "empty response"
    match = _JSON_BLOCK.search(raw_text)
    if not match:
        return None, "no JSON object found in response"
    try:
        data = json.loads(match.group(0))
    except ValueError as exc:
        return None, f"invalid JSON: {exc}"
    try:
        return Verdict.model_validate(data), None
    except Exception as exc:  # pydantic ValidationError and anything else
        return None, f"schema validation failed: {exc}"
