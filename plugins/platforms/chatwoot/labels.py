"""Predefined Chatwoot label taxonomy for CRWD Coach triage.

Swapping labels for another inbox: edit this list and add a matching skill.
Titles are lowercase — Chatwoot normalizes label titles to lowercase.
"""

from __future__ import annotations

from typing import Any, Dict, List

PREDEFINED_LABELS: List[Dict[str, Any]] = [
    {
        "title": "handoff-escalation",
        "description": "Bot called crwd_handoff — human looped in",
        "color": "#c0392b",
    },
    {
        "title": "mid-gig-support",
        "description": "Enrolled-gig conversation / mid-gig help",
        "color": "#47c479",
    },
    {
        "title": "proof-submission",
        "description": "Proof, receipt, or submission questions",
        "color": "#27ae60",
    },
    {
        "title": "gig-discovery",
        "description": "Browse gigs, apply, CRWD overview",
        "color": "#1f93ff",
    },
    {
        "title": "payment-payout",
        "description": "Payment timing, payout status, Dot",
        "color": "#ffc53d",
    },
    {
        "title": "account-eligibility",
        "description": "Account status, eligibility, opt-out, scam",
        "color": "#95a5a6",
    },
    {
        "title": "app-help",
        "description": "App navigation and broken UI",
        "color": "#7b68ee",
    },
    {
        "title": "off-topic",
        "description": "Non-CRWD requests",
        "color": "#aab7b8",
    },
]

PREDEFINED_LABEL_TITLES = frozenset(
    str(entry["title"]).strip().lower() for entry in PREDEFINED_LABELS
)
