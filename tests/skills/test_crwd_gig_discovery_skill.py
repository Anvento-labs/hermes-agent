"""Invariant tests for crwd-gig-discovery scope routing documentation."""

from __future__ import annotations

from pathlib import Path

SKILL_PATH = (
    Path(__file__).resolve().parents[2]
    / "skills"
    / "crwd"
    / "crwd-gig-discovery"
    / "SKILL.md"
)


def test_skill_documents_step_zero_scope_routing() -> None:
    text = SKILL_PATH.read_text()
    assert "Pick gig scope" in text
    assert "list_active_gigs" in text
    assert "get_user_gig_status" in text
    assert "Ambiguous" in text


def test_skill_ambiguous_section_uses_tools_not_prefetch_blocks() -> None:
    text = SKILL_PATH.read_text()
    ambiguous = text.split("## Ambiguous enrolled vs available", 1)[1]
    assert "get_user_gig_status" in ambiguous
    assert "[Gig intent guidance]" not in ambiguous
    assert "[CRWD gig context]" not in ambiguous


def test_skill_documents_bare_gig_phrases_as_ambiguous() -> None:
    text = SKILL_PATH.read_text()
    assert "list gigs" in text.lower()
    assert "give gigs" in text.lower()
    assert "mandatory" in text.lower()
