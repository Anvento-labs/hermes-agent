"""Scoped gig title linking for Chatwoot replies.

Gig page links are built at ``crwd_db`` fetch time (``attach_gig_url`` + ``gig_list_markdown``).
This module registers those fetch-time links and applies them when the model paraphrases
titles — bullet titles only before em/en dashes; comma lists become linked bullet lists.
Store names in line bodies are never globally replaced.
"""

from __future__ import annotations

import json
import re
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from tools.crwd_db_tool import _MATCH_FLOOR, _normalize, _score
from tools.crwd_urls import crwd_app_base_url, name_aliases

_FUZZY_LINK_FLOOR = max(_MATCH_FLOOR, 0.85)

_BULLET_TITLE_RE = re.compile(
    r"^(?P<indent>\s*[-*]\s+)(?P<title>.+?)(?P<sep>\s+[—–]\s+)(?P<body>.+)$",
    re.MULTILINE,
)
_GIG_MD_LINE_RE = re.compile(r"^- \[(?P<label>[^\]]+)\]\((?P<url>[^)]+)\)")

_turn_name_links: ContextVar[Dict[str, str]] = ContextVar("chatwoot_turn_name_links", default={})
_turn_gig_list_markdown: ContextVar[str] = ContextVar("chatwoot_turn_gig_list_markdown", default="")
_turn_md_pairs: ContextVar[List[Tuple[str, str]]] = ContextVar("chatwoot_turn_md_pairs", default=[])

_DEBUG_LOG_PATH = (
    Path(__file__).resolve().parents[3] / ".cursor" / "debug-c27c0e.log"
)


def _debug_log(hypothesis_id: str, location: str, message: str, data: dict) -> None:
    # #region agent log
    try:
        import time

        payload = {
            "sessionId": "c27c0e",
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        with open(_DEBUG_LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass
    # #endregion


def _is_chatwoot(platform: Any) -> bool:
    if str(platform or "").strip().lower() == "chatwoot":
        return True
    try:
        from gateway.session_context import get_session_env

        return (get_session_env("HERMES_SESSION_PLATFORM", "") or "").strip().lower() == "chatwoot"
    except Exception:
        return False


def reset_turn_registry() -> None:
    _turn_name_links.set({})
    _turn_gig_list_markdown.set("")
    _turn_md_pairs.set([])


def _registry() -> Dict[str, str]:
    return dict(_turn_name_links.get({}))


def _md_pairs() -> List[Tuple[str, str]]:
    return list(_turn_md_pairs.get([]))


def _link_with_label(stored_link: str, label: str) -> str:
    match = re.match(r"\[([^\]]+)\]\(([^)]+)\)", stored_link)
    if not match:
        return stored_link
    return f"[{label}]({match.group(2)})"


def record_name_link(name: Any, name_link: Any) -> None:
    link = str(name_link or "").strip()
    label = str(name or "").strip()
    if not label or not link:
        return
    current = dict(_turn_name_links.get({}))
    for key in name_aliases(label):
        current[key] = link
        current[key.casefold()] = link
    norm = _normalize(label)
    if norm:
        current[norm] = link
    _turn_name_links.set(current)


def _ingest_gig_list_markdown(md: str) -> None:
    md = (md or "").strip()
    if not md:
        return
    prev = _turn_gig_list_markdown.get("")
    combined = f"{prev}\n{md}".strip() if prev else md
    _turn_gig_list_markdown.set(combined)
    pairs = list(_turn_md_pairs.get([]))
    seen_urls = {url for _, url in pairs}
    for line in md.splitlines():
        match = _GIG_MD_LINE_RE.match(line.strip())
        if not match:
            continue
        label, url = match.group("label"), match.group("url")
        record_name_link(label, f"[{label}]({url})")
        if url not in seen_urls:
            pairs.append((label, url))
            seen_urls.add(url)
    _turn_md_pairs.set(pairs)


def record_links_from_payload(payload: Any) -> None:
    if isinstance(payload, list):
        for item in payload:
            record_links_from_payload(item)
        return
    if not isinstance(payload, dict):
        return

    plain = payload.get("name_plain") or payload.get("gig_name_plain")
    linked = payload.get("name") or payload.get("gig_name")
    if plain and linked and linked != plain:
        record_name_link(plain, linked)

    _ingest_gig_list_markdown(str(payload.get("gig_list_markdown") or ""))

    nested_gig = payload.get("gig")
    if isinstance(nested_gig, dict):
        record_links_from_payload(nested_gig)

    for key in ("items", "active_gigs"):
        for item in payload.get(key) or []:
            record_links_from_payload(item)


def _resolve_title_to_link(title: str, registry: Dict[str, str]) -> Optional[str]:
    stripped = title.strip().strip("*")
    if not stripped or "](http" in stripped:
        return None
    link = registry.get(stripped) or registry.get(stripped.casefold())
    if link:
        return _link_with_label(link, stripped)
    query_norm = _normalize(stripped)
    if query_norm:
        link = registry.get(query_norm) or registry.get(query_norm.casefold())
        if link:
            return _link_with_label(link, stripped)
    normalized = re.sub(r"\s*-\s*", " ", stripped).strip()
    for key, value in registry.items():
        if re.sub(r"\s*-\s*", " ", key).strip().casefold() == normalized.casefold():
            return _link_with_label(value, stripped)
    for plain, url in _md_pairs():
        for alias in name_aliases(plain):
            if alias.casefold() == stripped.casefold():
                return f"[{stripped}]({url})"
            if normalized and re.sub(r"\s*-\s*", " ", alias).strip().casefold() == normalized.casefold():
                return f"[{stripped}]({url})"
    if query_norm:
        best_url: Optional[str] = None
        best_score = 0.0
        for plain, url in _md_pairs():
            score = _score(query_norm, plain)
            if score > best_score:
                best_score = score
                best_url = url
        if best_url and best_score >= _FUZZY_LINK_FLOOR:
            return f"[{stripped}]({best_url})"
    return None


def _link_bullet_titles(text: str, registry: Dict[str, str]) -> str:
    def _repl(match: re.Match[str]) -> str:
        title = match.group("title")
        link = _resolve_title_to_link(title, registry)
        if not link:
            return match.group(0)
        return f"{match.group('indent')}{link}{match.group('sep')}{match.group('body')}"

    return _BULLET_TITLE_RE.sub(_repl, text)


def _comma_segments(line: str) -> List[str]:
    """Split on commas that are outside parentheses."""
    parts: List[str] = []
    current: List[str] = []
    depth = 0
    for ch in line:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            segment = "".join(current).strip()
            if segment:
                parts.append(segment)
            current = []
        else:
            current.append(ch)
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return parts


def _comma_line_linkable_count(line: str, registry: Dict[str, str]) -> int:
    return sum(1 for part in _comma_segments(line) if _resolve_title_to_link(part, registry))


def _is_comma_gig_line(line: str, registry: Dict[str, str]) -> bool:
    stripped = line.strip()
    if not stripped or stripped.startswith(("-", "*")):
        return False
    if "](http" in stripped:
        return False
    if "—" in stripped or "–" in stripped:
        return False
    if "," not in stripped:
        return False
    parts = _comma_segments(stripped)
    if len(parts) < 2:
        return False
    return _comma_line_linkable_count(stripped, registry) >= 2


def _comma_line_to_bullets(line: str, registry: Dict[str, str]) -> str:
    parts = _comma_segments(line)
    bullets: List[str] = []
    for part in parts:
        link = _resolve_title_to_link(part, registry)
        bullets.append(f"- {link}" if link else f"- {part}")
    return "\n".join(bullets)


def apply_scoped_gig_links(text: str, registry: Dict[str, str]) -> str:
    if not text or not crwd_app_base_url():
        return text
    if not registry and not _turn_gig_list_markdown.get(""):
        return text
    linked = _link_bullet_titles(text, registry)
    lines = linked.splitlines()
    out_lines: List[str] = []
    for line in lines:
        if _is_comma_gig_line(line, registry):
            out_lines.append(_comma_line_to_bullets(line, registry))
        else:
            out_lines.append(line)
    return "\n".join(out_lines)


def begin_turn_hook(**kwargs: Any) -> None:
    if not _is_chatwoot(kwargs.get("platform")):
        return
    reset_turn_registry()


def record_tool_links_hook(**kwargs: Any) -> None:
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
    _debug_log(
        "H1",
        "gig_links.record_tool_links_hook",
        "indexed crwd_db payload",
        {
            "item_count": len((payload.get("items") or []) if isinstance(payload, dict) else []),
            "registry_size": len(_registry()),
            "md_lines": len(_turn_gig_list_markdown.get("").splitlines()),
        },
    )


def apply_name_links_hook(**kwargs: Any) -> Optional[str]:
    """Apply fetch-time gig links when the model paraphrases titles."""
    if not _is_chatwoot(kwargs.get("platform")):
        return None
    if not crwd_app_base_url():
        return None
    response_text = str(kwargs.get("response_text") or "")
    if not response_text.strip():
        return None
    registry = _registry()
    if not registry and not _turn_gig_list_markdown.get(""):
        return None
    linked = apply_scoped_gig_links(response_text, registry)
    comma_lines = [ln for ln in response_text.splitlines() if _is_comma_gig_line(ln, registry)]
    comma_linkable = sum(_comma_line_linkable_count(ln, registry) for ln in comma_lines)
    _debug_log(
        "H2",
        "gig_links.apply_name_links_hook",
        "transform pass",
        {
            "registry_size": len(registry),
            "md_lines": len(_turn_gig_list_markdown.get("").splitlines()),
            "comma_lines": len(comma_lines),
            "comma_linkable": comma_linkable,
            "changed": linked != response_text,
            "output_links": linked.count("](http"),
        },
    )
    if linked == response_text:
        return None
    return linked
