"""Apply pre-built gig name_link markdown in Chatwoot replies.

Tool payloads include ``name_link`` at retrieval time (see ``tools/crwd_urls.py``).
This module collects those links during the turn and substitutes plain gig names in
the final assistant reply — without rewriting existing product/external markdown links.
"""

from __future__ import annotations

import json
import logging
import re
from contextvars import ContextVar
from typing import Any, Dict, List, Optional, Tuple

from tools.crwd_urls import crwd_app_base_url

logger = logging.getLogger(__name__)

_AUTO_LINK_RE = re.compile(r"<https?://[^>]+>")

# display key (DB name or alias) -> pre-built name_link markdown
_turn_name_links: ContextVar[Dict[str, str]] = ContextVar("chatwoot_turn_name_links", default={})


def _is_chatwoot(platform: Any) -> bool:
    if str(platform or "").strip().lower() == "chatwoot":
        return True
    try:
        from gateway.session_context import get_session_env

        return (get_session_env("HERMES_SESSION_PLATFORM", "") or "").strip().lower() == "chatwoot"
    except Exception:
        return False


def reset_turn_registry() -> None:
    """Clear collected name_link mappings for a new Chatwoot turn."""
    _turn_name_links.set({})


def _registry() -> Dict[str, str]:
    return dict(_turn_name_links.get({}))


def _name_aliases(name: str) -> List[str]:
    """Common paraphrase variants for the same gig title."""
    aliases = [name]
    collapsed = re.sub(r"\s*-\s*", " ", name).strip()
    if collapsed and collapsed not in aliases:
        aliases.append(collapsed)
    return aliases


def record_name_link(name: Any, name_link: Any) -> None:
    """Register one or more display keys for a pre-built name_link."""
    link = str(name_link or "").strip()
    label = str(name or "").strip()
    if not label or not link:
        return
    current = dict(_turn_name_links.get({}))
    for key in _name_aliases(label):
        current[key] = link
    _turn_name_links.set(current)


def record_links_from_payload(payload: Any) -> None:
    """Extract name_link fields from crwd_db JSON or gig context rows."""
    if isinstance(payload, list):
        for item in payload:
            record_links_from_payload(item)
        return
    if not isinstance(payload, dict):
        return

    name_link = payload.get("name_link")
    if name_link:
        record_name_link(payload.get("name") or payload.get("gig_name"), name_link)

    nested_gig = payload.get("gig")
    if isinstance(nested_gig, dict):
        record_links_from_payload(nested_gig)

    for key in ("items", "active_gigs"):
        for item in payload.get(key) or []:
            record_links_from_payload(item)


def _consume_bracket_or_link(text: str, start: int) -> int:
    """Return end index of a protected ``[...]`` or ``[...](...)`` span."""
    if start >= len(text) or text[start] != "[":
        return start + 1

    close = text.find("]", start + 1)
    if close == -1:
        return len(text)

    if close + 1 < len(text) and text[close + 1] == "(":
        depth = 1
        pos = close + 2
        while pos < len(text) and depth > 0:
            ch = text[pos]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            pos += 1
        return pos if depth == 0 else len(text)

    return close + 1


def _bare_url_end(text: str, start: int) -> int:
    if not text.startswith(("http://", "https://"), start):
        return start
    end = start
    while end < len(text) and text[end] not in " \t\n\r])<":
        end += 1
    return end


def _next_protected_start(text: str, pos: int) -> int:
    candidates: List[int] = []
    bracket = text.find("[", pos)
    if bracket != -1:
        candidates.append(bracket)
    angle = text.find("<", pos)
    if angle != -1:
        candidates.append(angle)
    for scheme in ("https://", "http://"):
        url = text.find(scheme, pos)
        if url != -1:
            candidates.append(url)
    return min(candidates) if candidates else len(text)


def _split_plain_and_protected(text: str) -> List[Tuple[str, bool]]:
    if not text:
        return []

    segments: List[Tuple[str, bool]] = []
    pos = 0
    while pos < len(text):
        protected_at = _next_protected_start(text, pos)
        if protected_at > pos:
            segments.append((text[pos:protected_at], True))
            pos = protected_at
            continue
        if pos >= len(text):
            break

        if text[pos] == "<":
            match = _AUTO_LINK_RE.match(text, pos)
            if match:
                segments.append((match.group(0), False))
                pos = match.end()
                continue

        if text[pos] == "[":
            end = _consume_bracket_or_link(text, pos)
            segments.append((text[pos:end], False))
            pos = end
            continue

        if text.startswith(("http://", "https://"), pos):
            end = _bare_url_end(text, pos)
            segments.append((text[pos:end], False))
            pos = end
            continue

        segments.append((text[pos], True))
        pos += 1

    return segments


def apply_name_links_in_text(text: str, links: Dict[str, str]) -> str:
    """Replace plain gig titles with pre-built name_link markdown."""
    if not text or not links or not crwd_app_base_url():
        return text

    pairs = [(name, link) for name, link in links.items() if name and link]
    if not pairs:
        return text

    pairs.sort(key=lambda item: len(item[0]), reverse=True)

    def _link_plain_segment(segment: str) -> str:
        names = [name for name, _ in pairs if name in segment]
        if not names:
            return segment
        link_by_name = {name: link for name, link in pairs}
        pattern = "|".join(re.escape(name) for name in names)

        def _repl(match: re.Match[str]) -> str:
            name = match.group(0)
            start, end = match.start(), match.end()
            if start > 0 and segment[start - 1] == "[":
                return name
            if end < len(segment) and segment[end] == "]":
                return name
            link = link_by_name[name]
            if link in segment:
                return name
            return link

        return re.sub(pattern, _repl, segment)

    out: List[str] = []
    for chunk, is_plain in _split_plain_and_protected(text):
        if is_plain:
            out.append(_link_plain_segment(chunk))
        else:
            out.append(chunk)
    return "".join(out)


def begin_turn_hook(**kwargs: Any) -> None:
    """``pre_llm_call`` — reset per-turn name_link registry on Chatwoot."""
    if not _is_chatwoot(kwargs.get("platform")):
        return
    reset_turn_registry()


def record_tool_links_hook(**kwargs: Any) -> None:
    """``post_tool_call`` — collect name_link values from ``crwd_db`` results."""
    if not _is_chatwoot(kwargs.get("platform")):
        return
    if str(kwargs.get("tool_name") or "").strip() != "crwd_db":
        return
    result = kwargs.get("result")
    if not isinstance(result, str) or not result.strip():
        return
    try:
        payload = json.loads(result)
    except json.JSONDecodeError:
        return
    record_links_from_payload(payload)


def apply_name_links_hook(**kwargs: Any) -> Optional[str]:
    """``transform_llm_output`` — paste name_link markdown for known gig titles."""
    if not _is_chatwoot(kwargs.get("platform")):
        return None
    if not crwd_app_base_url():
        return None

    response_text = str(kwargs.get("response_text") or "")
    if not response_text.strip():
        return None

    links = _registry()
    if not links:
        return None

    linked = apply_name_links_in_text(response_text, links)
    if linked == response_text:
        return None
    return linked
