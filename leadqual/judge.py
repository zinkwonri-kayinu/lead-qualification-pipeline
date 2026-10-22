"""Claude as the judge for leads the rules can't settle."""

from __future__ import annotations

import json
import os
from typing import Literal

import anthropic
from pydantic import BaseModel, Field

from .checks import Check

MODEL = os.environ.get("LEADQUAL_MODEL", "claude-opus-5-5")

SYSTEM = """You qualify B2B sales leads before they enter an outbound campaign.

You get the ideal customer profile (ICP), one lead's enriched data, the result of
deterministic checks, and any web research notes. Decide:

- valid: right company, person still works there, role can buy or champion, company fits the ICP
- invalid: wrong company, person has left, role can't buy, or company clearly outside the ICP
- unclear: the evidence conflicts or is too thin to decide; a human should look

Base the verdict only on the evidence given. Do not assume facts that are not there.
When checks fail softly (e.g. a partial company-name match), use the research notes
to resolve it if they can; otherwise say unclear."""


class Judgement(BaseModel):
    verdict: Literal["valid", "invalid", "unclear"]
    confidence: Literal["high", "medium", "low"]
    reason: str = Field(description="One sentence citing the evidence that decided it")


def build_prompt(lead: dict, checks: list[Check], icp: dict) -> str:
    check_lines = "\n".join(f"- {c.name}: {c.status} ({c.detail})" for c in checks)
    lead_view = {k: v for k, v in lead.items() if v and k != "research_notes"}
    return (
        f"ICP:\n{json.dumps(icp, indent=2)}\n\n"
        f"Lead:\n{json.dumps(lead_view, indent=2)}\n\n"
        f"Deterministic checks:\n{check_lines}\n\n"
        f"Research notes:\n{lead.get('research_notes') or 'none'}"
    )


def judge(client: anthropic.Anthropic, lead: dict, checks: list[Check], icp: dict) -> Judgement:
    response = client.beta.messages.parse(
        model=MODEL,
        max_tokens=1024,
        system=SYSTEM,
        messages=[{"role": "user", "content": build_prompt(lead, checks, icp)}],
        output_config={"effort": "low"},
        output_format=Judgement,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason == "refusal" or response.parsed_output is None:
        return Judgement(verdict="unclear", confidence="low", reason="model declined or returned no verdict")
    return response.parsed_output
