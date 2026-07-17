"""Predefined Chatwoot label taxonomy for CRWD Coach triage.

Swapping labels for another inbox: edit these lists and add a matching skill.
Titles are lowercase — Chatwoot normalizes label titles to lowercase.

``APPLIED_LABELS`` are assigned by auto-labeling / skills and bootstrapped into
Chatwoot when missing. ``UNAPPLIED_LABELS`` are kept for future reactivation —
they are never assigned and never created on Chatwoot by bootstrap.
"""

from __future__ import annotations

from typing import Any, Dict, List

# Actively assigned (auto classifier, hard tool signals, or teammate skills).
APPLIED_LABELS: List[Dict[str, Any]] = [
    {
        "title": "handoff-escalation",
        "description": "Bot called crwd_handoff — human looped in",
        "color": "#c0392b",
    },
    {
        "title": "gig-complete",
        "description": "Every proof the gig requires has been accepted",
        "color": "#16a085",
    },
    {
        "title": "payment-issue",
        "description": "Any payment-related question or message",
        "color": "#ffc53d",
    },
    {
        "title": "app-help",
        "description": "App navigation and broken UI",
        "color": "#7b68ee",
    },
    {
        "title": "proof-rejection",
        "description": "At least one proof stored this turn was rejected",
        "color": "#e74c3c",
    },
    {
        "title": "proof-acceptance",
        "description": "All proofs stored this turn were accepted",
        "color": "#27ae60",
    },
    {
        "title": "new-user",
        "description": "Member has not yet completed a gig (required proofs accepted)",
        "color": "#3498db",
    },
    # Fraud risk bands -- mutually exclusive, owned by crwd-risk-analyser and
    # derived from the contact's risk_score. Never shown to the member. Unlike
    # topic labels, these are not classified per turn: labels_auto preserves
    # them (see _PRESERVED_PREFIXES) rather than re-deriving them.
    {
        "title": "risk-low",
        "description": "Fraud risk 0-30",
        "color": "#7f8c8d",
    },
    {
        "title": "risk-medium",
        "description": "Fraud risk 30-60 — manual review recommended",
        "color": "#f39c12",
    },
    {
        "title": "risk-high",
        "description": "Fraud risk 60-85 — manual approval required",
        "color": "#e67e22",
    },
    {
        "title": "risk-critical",
        "description": "Fraud risk 85-100 — block or reject",
        "color": "#8e44ad",
    },
]

# Kept for future reactivation — not assigned, not bootstrapped to Chatwoot.
UNAPPLIED_LABELS: List[Dict[str, Any]] = [
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
        "description": "Browse gigs, find available gigs, apply to specific gigs",
        "color": "#1f93ff",
    },
    {
        "title": "general-inquiry",
        "description": "General CRWD / app questions (what it is, how it works, apply, what gigs are)",
        "color": "#3498db",
    },
    {
        "title": "payment-payout",
        "description": "Payment timing, payout status, Dot (superseded by payment-issue)",
        "color": "#ffc53d",
    },
    {
        "title": "account-eligibility",
        "description": "Eligibility to join or qualify for CRWD/gigs",
        "color": "#95a5a6",
    },
    {
        "title": "account-info",
        "description": "Account status, membership, ban/suspension",
        "color": "#7f8c8d",
    },
    {
        "title": "scam",
        "description": "Scam, phishing, fraud, unauthorized other-user data asks, impersonation, or jailbreak",
        "color": "#e74c3c",
    },
    {
        "title": "off-topic",
        "description": "Non-CRWD requests",
        "color": "#aab7b8",
    },
]

APPLIED_LABEL_TITLES = frozenset(
    str(entry["title"]).strip().lower() for entry in APPLIED_LABELS
)
UNAPPLIED_LABEL_TITLES = frozenset(
    str(entry["title"]).strip().lower() for entry in UNAPPLIED_LABELS
)

# Full catalog (docs / awareness). Bootstrap and assignment use APPLIED only.
PREDEFINED_LABELS: List[Dict[str, Any]] = list(APPLIED_LABELS) + list(UNAPPLIED_LABELS)
PREDEFINED_LABEL_TITLES = APPLIED_LABEL_TITLES | UNAPPLIED_LABEL_TITLES
