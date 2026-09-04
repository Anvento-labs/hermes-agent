"""Contract tests for campaign-code replies in crwd-gig-discovery."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DISCOVERY = ROOT / "skills/crwd/crwd-gig-discovery/SKILL.md"
STAGES = ROOT / "skills/crwd/crwd-reference/references/gig-stages.md"


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text).lower()


def _section(markdown: str, heading: str) -> str:
    pattern = rf"^## {re.escape(heading)}\s*\n(.*?)(?=^## |\Z)"
    match = re.search(pattern, markdown, flags=re.MULTILINE | re.DOTALL)
    assert match is not None, f"missing ## {heading}"
    return match.group(1)


def test_description_mentions_campaign_code():
    text = DISCOVERY.read_text()
    fm = _flat(text.split("---", 2)[1])
    assert "campaign code" in fm


def test_when_to_use_includes_campaign_code_not_sender_checks():
    when = _section(DISCOVERY.read_text(), "When to Use").lower()
    assert "campaign code" in when
    assert "is this number crwd" not in when
    assert "is this text from crwd" not in when


def test_procedure_lookup_before_fuzzy_and_records_interest():
    procedure = _flat(_section(DISCOVERY.read_text(), "Procedure"))
    assert "lookup_campaign_code" in procedure
    assert "add_user_gig_interest" in procedure
    assert "get_gig_details" in procedure
    assert "fuzzy-match" in procedure
    assert "match any active campaign" in procedure
    assert "list_active_gigs" in procedure


def test_pitfalls_forbid_fuzzy_first_and_internal_names():
    pitfalls = _flat(_section(DISCOVERY.read_text(), "Pitfalls"))
    assert "lookup_campaign_code" in pitfalls or "not a gig name" in pitfalls
    assert "mongo" in pitfalls
    assert "field names" in pitfalls


def test_verification_requires_lookup_and_interest():
    verification = _flat(_section(DISCOVERY.read_text(), "Verification"))
    assert "lookup_campaign_code" in verification
    assert "add_user_gig_interest" in verification


def test_gig_stages_interest_includes_campaign_code():
    text = _flat(STAGES.read_text())
    assert "lookup_campaign_code" in text
    assert "add_user_gig_interest" in text
