"""Contract tests for the bundled crwd-lead-intro skill."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills/crwd/crwd-lead-intro/SKILL.md"

REQUIRED_HEADINGS = (
    "When to Use",
    "Prerequisites",
    "How to Run",
    "Procedure",
    "Pitfalls",
    "Verification",
)


def _frontmatter(text: str) -> str:
    parts = text.split("---", 2)
    assert len(parts) >= 3
    return parts[1]


def test_description_is_one_short_sentence():
    text = SKILL.read_text()
    match = re.search(r"^description:\s*[\"']?(.*)[\"']?\s*$", _frontmatter(text), re.MULTILINE)
    assert match, "missing description"
    desc = match.group(1).strip().strip("\"'")
    assert len(desc) <= 60, len(desc)
    assert desc.endswith(".")


def test_required_sections_present():
    text = SKILL.read_text()
    for heading in REQUIRED_HEADINGS:
        assert re.search(rf"^## {re.escape(heading)}\s*$", text, re.MULTILINE), heading


def test_uses_crwd_db_and_user_created():
    body = SKILL.read_text().lower()
    assert "crwd_db" in body
    assert "user_created" in body
    assert "get_gig_details" in body or "get_user_gig_status" in body


def test_forbids_frozen_template_as_only_reply():
    text = SKILL.read_text().lower()
    assert "frozen" in text or "canned" in text or "template" in text
    assert "one" in text and "reply" in text
