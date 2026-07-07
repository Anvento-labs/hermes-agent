"""app-chatbot plugin — CLI prefetch hook for CRWD MongoDB queries via crwd_db."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from tools import crwd_db_tool as crwd

from ._utils import default_user_id
from .router import format_router_context

logger = logging.getLogger(__name__)


def _crwd_db_available() -> bool:
    return crwd.check_crwd_db_requirements()


def _plugin_settings() -> Dict[str, Any]:
    return {"default_user_id": default_user_id()}


def _prefetch_context(user_message: str = "", **kwargs: Any) -> Optional[Dict[str, str]]:
    if str(kwargs.get("platform") or "").strip().lower() == "chatwoot":
        return None
    if not _crwd_db_available():
        return None
    settings = _plugin_settings()
    context = format_router_context(
        user_message,
        default_user_id=settings["default_user_id"],
    )
    if not context:
        return None
    return {"context": context}


def register(ctx) -> None:
    ctx.register_hook("pre_llm_call", _prefetch_context)
