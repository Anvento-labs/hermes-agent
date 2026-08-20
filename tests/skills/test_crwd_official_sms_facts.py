"""Contract tests for official CRWD SMS numbers in bundled facts."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FACTS = ROOT / "skills/crwd/crwd-reference/references/company-facts.md"
REFERENCE = ROOT / "skills/crwd/crwd-reference/SKILL.md"
DISCOVERY = ROOT / "skills/crwd/crwd-gig-discovery/SKILL.md"

OFFICIAL_LAST10 = (
    "8187177186",
    "8187177190",
    "8187177193",
    "8187177164",
    "8187177110",
)


def _digits(text: str) -> str:
    return re.sub(r"\D+", "", text)


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text).lower()


def _section(markdown: str, heading: str) -> str:
    pattern = rf"^## {re.escape(heading)}\s*\n(.*?)(?=^## |\Z)"
    match = re.search(pattern, markdown, flags=re.MULTILINE | re.DOTALL)
    assert match is not None, f"missing ## {heading}"
    return match.group(1)


def test_company_facts_lists_every_official_number():
    digits = _digits(FACTS.read_text())
    for last10 in OFFICIAL_LAST10:
        assert last10 in digits, f"{last10} missing from company-facts.md"


def test_company_facts_says_crwd_does_send_gig_texts():
    text = FACTS.read_text().lower()
    assert "does" in text and "send" in text
    assert "official" in text


def test_matching_sender_missing_from_app_is_not_ignored():
    section = _section(FACTS.read_text(), "Official SMS numbers").lower()
    assert "don't see the gig in the app" in section
    assert "not" in section and "ignore" in section


def test_company_facts_never_tell_member_scam_rule():
    section = _flat(_section(FACTS.read_text(), "Official SMS numbers"))
    assert "never tell a member" in section
    assert "might be a scam" in section


def test_crwd_reference_description_mentions_official_sms():
    text = REFERENCE.read_text()
    fm = _flat(text.split("---", 2)[1])
    when = _flat(_section(text, "When to Use"))
    assert "official sms" in fm
    assert "is this text from crwd" not in fm
    assert "company-facts.md" in when


def test_gig_discovery_when_to_use_does_not_own_sender_number_checks():
    when = _section(DISCOVERY.read_text(), "When to Use").lower()
    assert "is this number crwd" not in when
    assert "is this text from crwd" not in when


def test_gig_discovery_pitfall_defers_sms_numbers_to_reference():
    pitfalls = _flat(_section(DISCOVERY.read_text(), "Pitfalls"))
    assert "company-facts.md" in pitfalls
    assert "matching official number is still from crwd" in pitfalls
