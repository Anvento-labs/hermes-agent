"""CRWD MongoDB tool package — implementation for ``crwd_db``.

The discovery shim ``tools/crwd_db_tool.py`` registers the tool and re-exports
the public API used by Chatwoot and app-chatbot.
"""

from tools.crwd_db.connection import _db, _oid, check_crwd_db_requirements, reset_client
from tools.crwd_db.prefetch import (
    fetch_active_gigs,
    fetch_gig_details,
    fetch_user_gig_history,
    fetch_user_joined_gigs,
    fetch_user_profile,
    fetch_waitlisted_gigs,
)
from tools.crwd_db.proofs import user_has_completed_gig
from tools.crwd_db.router import crwd_db_tool
from tools.crwd_db.schema import CRWD_DB_SCHEMA
from tools.crwd_db.stage import build_user_gig_status, compute_gig_stage

__all__ = [
    "CRWD_DB_SCHEMA",
    "build_user_gig_status",
    "check_crwd_db_requirements",
    "compute_gig_stage",
    "crwd_db_tool",
    "fetch_active_gigs",
    "fetch_gig_details",
    "fetch_user_gig_history",
    "fetch_user_joined_gigs",
    "fetch_user_profile",
    "fetch_waitlisted_gigs",
    "reset_client",
    "user_has_completed_gig",
    "_db",
    "_oid",
]
