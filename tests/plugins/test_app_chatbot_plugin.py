"""Tests for the app-chatbot CLI prefetch plugin."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PLUGIN_DIR = _REPO_ROOT / "plugins" / "app-chatbot"
_PKG_NAME = "hermes_plugins.app_chatbot"


def _plugin_dir() -> Path:
    return _PLUGIN_DIR


def _purge_plugin_modules() -> None:
    for key in list(sys.modules):
        if key == _PKG_NAME or key.startswith(f"{_PKG_NAME}."):
            del sys.modules[key]


def _ensure_plugin_package():
    plugin_dir = _plugin_dir()
    if _PKG_NAME not in sys.modules:
        if "hermes_plugins" not in sys.modules:
            ns = types.ModuleType("hermes_plugins")
            ns.__path__ = []
            sys.modules["hermes_plugins"] = ns
        pkg = types.ModuleType(_PKG_NAME)
        pkg.__path__ = [str(plugin_dir)]
        pkg.__package__ = _PKG_NAME
        sys.modules[_PKG_NAME] = pkg

    load_order = ("_utils", "router", "prefetch")
    for sub in load_order:
        fq = f"{_PKG_NAME}.{sub}"
        if fq in sys.modules:
            continue
        sub_path = plugin_dir / f"{sub}.py"
        if not sub_path.exists():
            continue
        spec = importlib.util.spec_from_file_location(fq, sub_path)
        submod = importlib.util.module_from_spec(spec)
        submod.__package__ = _PKG_NAME
        sys.modules[fq] = submod
        spec.loader.exec_module(submod)
    return sys.modules[_PKG_NAME]


def _load_module(name: str):
    _ensure_plugin_package()
    fq = f"{_PKG_NAME}.{name}"
    if name.endswith(".py"):
        name = name[:-3]
    if not fq.startswith("hermes_plugins"):
        fq = f"{_PKG_NAME}.{name}"
    return sys.modules[fq]


def _load_plugin_init():
    _ensure_plugin_package()
    plugin_dir = _plugin_dir()
    if _PKG_NAME in sys.modules and hasattr(sys.modules[_PKG_NAME], "register"):
        return sys.modules[_PKG_NAME]
    spec = importlib.util.spec_from_file_location(
        _PKG_NAME,
        plugin_dir / "__init__.py",
        submodule_search_locations=[str(plugin_dir)],
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = _PKG_NAME
    mod.__path__ = [str(plugin_dir)]
    sys.modules[_PKG_NAME] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _isolate_env(tmp_path, monkeypatch):
    _purge_plugin_modules()
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://localhost:27017")
    monkeypatch.setenv("CRWD_DEFAULT_USER_ID", "69a6f191cb29b0b371b3a156")
    yield hermes_home


class TestUtils:
    def test_parse_object_id_valid(self):
        utils = _load_module("_utils")
        oid = utils.parse_object_id("69a6f191cb29b0b371b3a156")
        assert str(oid) == "69a6f191cb29b0b371b3a156"

    def test_parse_object_id_invalid(self):
        utils = _load_module("_utils")
        with pytest.raises(ValueError):
            utils.parse_object_id("not-an-id")

    def test_default_user_id_prefers_crwd_env(self, monkeypatch):
        utils = _load_module("_utils")
        monkeypatch.setenv("CRWD_DEFAULT_USER_ID", "aaaaaaaaaaaaaaaaaaaaaaaa")
        monkeypatch.setenv("APP_CHATBOT_DEFAULT_USER_ID", "bbbbbbbbbbbbbbbbbbbbbbbb")
        assert utils.default_user_id() == "aaaaaaaaaaaaaaaaaaaaaaaa"

    def test_default_user_id_falls_back_to_legacy_env(self, monkeypatch):
        utils = _load_module("_utils")
        monkeypatch.delenv("CRWD_DEFAULT_USER_ID", raising=False)
        monkeypatch.setenv("APP_CHATBOT_DEFAULT_USER_ID", "69a6f191cb29b0b371b3a156")
        assert utils.default_user_id() == "69a6f191cb29b0b371b3a156"


class TestRouter:
    def test_active_gigs_intent(self):
        router = _load_module("router")
        with patch(
            "tools.crwd_db_tool.fetch_active_gigs",
            return_value={"_type": "gig_list", "items": [], "error": None},
        ) as mock_fn:
            result = router.route_intent("What active gigs can I join?", "69a6f191cb29b0b371b3a156")
        assert result["action"] == "list_active_gigs"
        mock_fn.assert_called_once()

    def test_joined_gigs_intent(self):
        router = _load_module("router")
        with patch(
            "tools.crwd_db_tool.fetch_user_joined_gigs",
            return_value={"_type": "user_gigs", "items": [], "error": None},
        ) as mock_fn:
            result = router.route_intent("Show my joined gigs", "69a6f191cb29b0b371b3a156")
        assert result["action"] == "get_user_gigs"
        mock_fn.assert_called_once()

    def test_waitlisted_gigs_intent(self):
        router = _load_module("router")
        with patch(
            "tools.crwd_db_tool.fetch_waitlisted_gigs",
            return_value={"_type": "waitlisted_gigs", "items": [], "error": None},
        ) as mock_fn:
            result = router.route_intent("What are my waitlisted gigs?", "69a6f191cb29b0b371b3a156")
        assert result["action"] == "get_waitlisted_gigs"
        mock_fn.assert_called_once()

    def test_history_intent(self):
        router = _load_module("router")
        with patch(
            "tools.crwd_db_tool.fetch_user_gig_history",
            return_value={"_type": "user_gig_history", "items": [], "error": None},
        ) as mock_fn:
            result = router.route_intent("Show my gig history", "69a6f191cb29b0b371b3a156")
        assert result["action"] == "get_user_gig_history"
        mock_fn.assert_called_once()

    def test_no_match_returns_none(self):
        router = _load_module("router")
        assert router.route_intent("hello world random chat", "69a6f191cb29b0b371b3a156") is None

    def test_identity_question_not_routed_as_gig(self):
        router = _load_module("router")
        assert router.route_intent("tell me about u", "69a6f191cb29b0b371b3a156") is None
        assert router.route_intent("who are you", "69a6f191cb29b0b371b3a156") is None

    def test_format_router_context_includes_user_line(self):
        router = _load_module("router")
        ctx = router.format_router_context("hello", default_user_id="69a6f191cb29b0b371b3a156")
        assert "Current CLI user_id" in ctx
        assert "CRWD_DEFAULT_USER_ID" in ctx

    def test_format_router_context_includes_data_access_policy(self):
        router = _load_module("router")
        ctx = router.format_router_context("hello", default_user_id="69a6f191cb29b0b371b3a156")
        assert "[Data access policy]" in ctx
        assert "crwd_db" in ctx
        assert "Do not attempt direct MongoDB queries" in ctx


class TestPluginRegistration:
    def test_register_wires_hook_only(self):
        plugin = _load_plugin_init()
        ctx = MagicMock()
        plugin.register(ctx)
        ctx.register_hook.assert_called_once_with("pre_llm_call", plugin._prefetch_context)
        ctx.register_tool.assert_not_called()

    def test_prefetch_context_returns_none_without_crwd_uri(self, monkeypatch):
        plugin = _load_plugin_init()
        monkeypatch.delenv("CRWD_MONGO_URI", raising=False)
        monkeypatch.delenv("MONGODB_URI", raising=False)
        assert plugin._prefetch_context(user_message="active gigs") is None

    def test_prefetch_context_returns_dict_when_routed(self, monkeypatch):
        plugin = _load_plugin_init()
        with patch.object(
            plugin,
            "format_router_context",
            return_value="[Database Context]\nfoo",
        ):
            result = plugin._prefetch_context(user_message="active gigs")
        assert result == {"context": "[Database Context]\nfoo"}

    def test_prefetch_context_uses_env_default_user_id(self):
        plugin = _load_plugin_init()
        with patch.object(plugin, "format_router_context", return_value="ctx") as mock_fmt:
            plugin._prefetch_context(user_message="active gigs")
        mock_fmt.assert_called_once_with("active gigs", default_user_id="69a6f191cb29b0b371b3a156")
