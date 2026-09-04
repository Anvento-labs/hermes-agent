"""Shim for tool discovery. Registers ``crwd_db`` with tools.registry.

The real implementation lives in the ``tools/crwd_db/`` package to keep the
file structure clean. This shim exists because tools.registry auto-imports
``tools/*.py`` — we need a top-level module to trigger the registration.

Also re-exports the public API used by Chatwoot enrichment/labels, app-chatbot,
and unit tests so existing ``from tools.crwd_db_tool import …`` / ``t.foo``
paths keep working.
"""

from __future__ import annotations

from tools.crwd_db import connection
from tools.crwd_db import gigs as _gigs
from tools.crwd_db import proofs as _proofs
from tools.crwd_db import stage as _stage
from tools.crwd_db.connection import (
    _ALLOWED_COLLECTIONS,
    _HARD_LIMIT,
    _MATCH_FLOOR,
    _USER_FIELDS,
    _db,
    _id_values,
    _oid,
    _resolve_mongo_uri,
    check_crwd_db_requirements,
    reset_client,
)
from tools.crwd_db.users import _create_user, _get_user
from tools.crwd_db.custom_query import _has_where, _redact_secrets
from tools.crwd_db.gigs import (
    _STORE_REQUIREMENT_FLAGS,
    _campaign_code_query,
    _effective_payout,
    _full_gig,
    _get_enrolled_gig_ids,
    _lookup_campaign_code,
    _normalize,
    _open_gig_filter,
    _score,
    _slim_gig,
    _spots_full_gig_oids,
)
from tools.crwd_db.membership import (
    _add_user_gig_interest,
    _joined_member_filter,
    _mark_membership_approved,
    _sort_members_by_gig_end_date,
)
from tools.crwd_db.prefetch import (
    fetch_active_gigs,
    fetch_gig_details,
    fetch_user_gig_history,
    fetch_user_joined_gigs,
    fetch_user_profile,
    fetch_waitlisted_gigs,
)
from tools.crwd_db.proofs import (
    _PROOF_REASON_CODES,
    _PROOF_TYPES,
    _artifacts_for,
    _normalize_proof_id,
    user_has_completed_gig,
)
from tools.crwd_db.router import crwd_db_tool
from tools.crwd_db.schema import CRWD_DB_SCHEMA
from tools.crwd_db.stage import (
    _collect_buy_products,
    _progress_for_crwd,
    build_user_gig_status,
    compute_gig_stage,
)
from tools.registry import registry

# Submodules exposed so tests can patch.object(t.connection, "_db", …) etc.
gigs = _gigs
proofs = _proofs
stage = _stage

registry.register(
    name="crwd_db",
    toolset="crwd",
    schema=CRWD_DB_SCHEMA,
    handler=crwd_db_tool,
    check_fn=check_crwd_db_requirements,
    requires_env=["CRWD_MONGO_URI"],
    emoji="🛍️",
)

__all__ = [
    "CRWD_DB_SCHEMA",
    "build_user_gig_status",
    "check_crwd_db_requirements",
    "compute_gig_stage",
    "connection",
    "crwd_db_tool",
    "fetch_active_gigs",
    "fetch_gig_details",
    "fetch_user_gig_history",
    "fetch_user_joined_gigs",
    "fetch_user_profile",
    "fetch_waitlisted_gigs",
    "gigs",
    "proofs",
    "reset_client",
    "stage",
    "user_has_completed_gig",
    "_ALLOWED_COLLECTIONS",
    "_HARD_LIMIT",
    "_MATCH_FLOOR",
    "_PROOF_REASON_CODES",
    "_PROOF_TYPES",
    "_STORE_REQUIREMENT_FLAGS",
    "_USER_FIELDS",
    "_artifacts_for",
    "_collect_buy_products",
    "_db",
    "_effective_payout",
    "_full_gig",
    "_get_enrolled_gig_ids",
    "_add_user_gig_interest",
    "_mark_membership_approved",
    "_create_user",
    "_get_user",
    "_has_where",
    "_id_values",
    "_joined_member_filter",
    "_normalize",
    "_normalize_proof_id",
    "_oid",
    "_open_gig_filter",
    "_progress_for_crwd",
    "_redact_secrets",
    "_resolve_mongo_uri",
    "_score",
    "_slim_gig",
    "_sort_members_by_gig_end_date",
    "_spots_full_gig_oids",
    "_campaign_code_query",
    "_lookup_campaign_code",
]
