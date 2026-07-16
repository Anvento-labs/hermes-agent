"""Tests for automatic Chatwoot conversation labeling."""

from unittest.mock import patch

import pytest

from plugins.platforms.chatwoot import labels_auto as auto


class TestClassifyConversationLabels:
    def test_find_gigs(self):
        labels = auto.classify_conversation_labels("what gigs are near me?")
        assert "gig-discovery" in labels

    def test_browse_while_enrolled_stays_discovery(self):
        with patch.object(
            auto,
            "_member_has_active_gigs",
            return_value=(True, {"Amazon Gig"}),
        ):
            labels = auto.classify_conversation_labels(
                "what gigs are near me?",
                contact_id="contact-1",
            )
        assert labels == ["gig-discovery"]
        assert "mid-gig-support" not in labels

    def test_payment(self):
        labels = auto.classify_conversation_labels("did I get paid yet?")
        assert "payment-payout" in labels

    def test_multi_label_payment_and_app_help(self):
        labels = auto.classify_conversation_labels(
            "my payout page won't load, when will I get paid?"
        )
        assert labels == ["payment-payout", "app-help"]

    def test_off_topic_fallback(self):
        labels = auto.classify_conversation_labels("hello there")
        assert labels == ["off-topic"]

    def test_no_handoff_from_frustration_keywords(self):
        labels = auto.classify_conversation_labels("I'm so frustrated, get me a human")
        assert "handoff-escalation" not in labels

    def test_handoff_only_when_requested(self):
        with patch.object(
            auto,
            "_member_has_active_gigs",
            return_value=(True, {"Some Gig"}),
        ):
            labels = auto.classify_conversation_labels(
                "my submission was rejected",
                handoff_requested=True,
                contact_id="contact-1",
            )
        assert labels == ["proof-submission", "handoff-escalation"]

    def test_proof_without_enrollment(self):
        with patch.object(auto, "_member_has_active_gigs", return_value=(False, set())):
            labels = auto.classify_conversation_labels(
                "how do I submit proof?",
                contact_id="contact-1",
            )
        assert labels == ["proof-submission"]

    def test_proof_with_enrollment(self):
        with patch.object(
            auto,
            "_member_has_active_gigs",
            return_value=(True, {"Amazon Gig"}),
        ):
            labels = auto.classify_conversation_labels(
                "how do I submit proof?",
                contact_id="contact-1",
            )
        assert labels == ["proof-submission", "mid-gig-support"]

    def test_mid_gig_unenrolled_falls_back_to_discovery(self):
        with patch.object(auto, "_member_has_active_gigs", return_value=(False, set())):
            labels = auto.classify_conversation_labels(
                "what's my deadline on the amazon gig?",
                contact_id="contact-1",
            )
        assert labels == ["gig-discovery"]

    def test_mid_gig_enrolled(self):
        with patch.object(
            auto,
            "_member_has_active_gigs",
            return_value=(True, {"Amazon Gig"}),
        ):
            labels = auto.classify_conversation_labels(
                "what's my deadline on the amazon gig?",
                contact_id="contact-1",
            )
        assert labels == ["mid-gig-support"]

    def test_mid_gig_enrolled_nameless(self):
        with patch.object(
            auto,
            "_member_has_active_gigs",
            return_value=(True, {"Amazon Gig"}),
        ):
            labels = auto.classify_conversation_labels(
                "what's my deadline?",
                contact_id="contact-1",
            )
        assert labels == ["mid-gig-support"]

    def test_mid_gig_named_unmatched_is_discovery(self):
        with patch.object(
            auto,
            "_member_has_active_gigs",
            return_value=(True, {"Target Gig"}),
        ):
            labels = auto.classify_conversation_labels(
                "what's my deadline on the amazon gig?",
                contact_id="contact-1",
            )
        assert labels == ["gig-discovery"]

    def test_unenrolled_gig_details_is_discovery(self):
        with patch.object(auto, "_member_has_active_gigs", return_value=(False, set())):
            labels = auto.classify_conversation_labels(
                "give me details about the amazon gig ?",
                contact_id="contact-1",
            )
        assert labels == ["gig-discovery"]

    def test_opt_out_topic_without_handoff(self):
        labels = auto.classify_conversation_labels("stop texting me")
        assert labels == ["account-eligibility"]
        assert "handoff-escalation" not in labels

    def test_opt_out_with_handoff(self):
        labels = auto.classify_conversation_labels(
            "stop texting me",
            handoff_requested=True,
        )
        assert labels == ["account-eligibility", "handoff-escalation"]


class TestHandoffDetection:
    def test_handoff_tool_hook_sets_flag(self):
        auto.reset_handoff_flag()
        auto.handoff_tool_hook(platform="chatwoot", tool_name="crwd_handoff")
        assert auto.handoff_requested_this_turn() is True

    def test_handoff_tool_hook_ignores_other_tools(self):
        auto.reset_handoff_flag()
        auto.handoff_tool_hook(platform="chatwoot", tool_name="crwd_db")
        assert auto.handoff_requested_this_turn() is False

    def test_handoff_in_history(self):
        history = [
            {"role": "user", "content": "help me"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "crwd_handoff", "arguments": "{}"},
                    }
                ],
            },
        ]
        assert auto._handoff_in_current_turn(history, "help me") is True


class TestAutoLabelConversation:
    @pytest.fixture
    def chatwoot_env(self, monkeypatch):
        monkeypatch.setenv("CHATWOOT_BASE_URL", "https://chat.example.com")
        monkeypatch.setenv("CHATWOOT_AGENT_TOKEN", "agent-tok")
        monkeypatch.setenv("CHATWOOT_ACCOUNT_ID", "1")

    def test_skips_without_creds(self, monkeypatch):
        monkeypatch.delenv("CHATWOOT_BASE_URL", raising=False)
        out = auto.auto_label_conversation("hello")
        assert out["skipped"] is True

    def test_applies_labels(self, chatwoot_env):
        with patch.object(auto, "_resolve_conversation", return_value=("1", "42")), patch.object(
            auto, "_conversation_has_handoff_label", return_value=False,
        ), patch.object(
            auto, "_create_labels_if_not_exists",
            return_value={"success": True, "existing": ["proof-submission"]},
        ), patch.object(
            auto, "_member_has_active_gigs", return_value=(False, set()),
        ), patch.object(
            auto, "_assign_labels",
            return_value={"success": True, "labels": ["proof-submission"], "error": None},
        ) as assign:
            out = auto.auto_label_conversation("how do I submit proof?")
        assert out["success"] is True
        assert out["classified"] == ["proof-submission"]
        assign.assert_called_once_with("1", "42", ["proof-submission"], replace=True)

    def test_sticky_handoff_preserved_on_later_turn(self, chatwoot_env):
        """A conversation already escalated keeps handoff-escalation even when
        the current turn doesn't call crwd_handoff (regression: add-then-remove)."""
        with patch.object(auto, "_resolve_conversation", return_value=("1", "42")), patch.object(
            auto, "_conversation_has_handoff_label", return_value=True,
        ), patch.object(
            auto, "_create_labels_if_not_exists",
            return_value={"success": True, "existing": ["payment-payout"]},
        ), patch.object(
            auto, "_member_has_active_gigs", return_value=(False, set()),
        ), patch.object(
            auto, "_assign_labels",
            return_value={"success": True, "labels": [], "error": None},
        ) as assign:
            # No handoff_requested this turn — just a follow-up message.
            out = auto.auto_label_conversation("ok thank you", handoff_requested=False)
        assert out["classified"] == ["off-topic", "handoff-escalation"]
        assign.assert_called_once_with(
            "1", "42", ["off-topic", "handoff-escalation"], replace=True
        )

    def test_handoff_this_turn_does_not_need_lookup(self, chatwoot_env):
        """When handoff fired this turn, the sticky lookup is short-circuited."""
        with patch.object(auto, "_resolve_conversation", return_value=("1", "42")), patch.object(
            auto, "_conversation_has_handoff_label",
        ) as sticky, patch.object(
            auto, "_create_labels_if_not_exists",
            return_value={"success": True, "existing": []},
        ), patch.object(
            auto, "_member_has_active_gigs", return_value=(False, set()),
        ), patch.object(
            auto, "_assign_labels",
            return_value={"success": True, "labels": [], "error": None},
        ) as assign:
            out = auto.auto_label_conversation(
                "my payout never came", handoff_requested=True
            )
        sticky.assert_not_called()
        assert out["classified"] == ["payment-payout", "handoff-escalation"]
        assign.assert_called_once_with(
            "1", "42", ["payment-payout", "handoff-escalation"], replace=True
        )


class TestAutoLabelHook:
    def test_ignores_non_chatwoot(self):
        with patch.object(auto, "auto_label_conversation") as fn:
            auto.auto_label_hook(platform="telegram", user_message="hi")
        fn.assert_not_called()

    def test_runs_on_chatwoot(self):
        with patch.object(auto, "auto_label_conversation") as fn:
            auto.auto_label_hook(
                platform="chatwoot",
                user_message="how do I submit proof?",
            )
        fn.assert_called_once()

    def test_passes_handoff_flag(self):
        auto.reset_handoff_flag()
        auto.reset_contact_id()
        auto._contact_id_this_turn.set("99")
        auto.handoff_tool_hook(tool_name="crwd_handoff")
        with patch.object(auto, "auto_label_conversation") as fn:
            auto.auto_label_hook(
                platform="chatwoot",
                user_message="stop texting me",
            )
        fn.assert_called_once_with(
            user_message="stop texting me",
            conversation_history=None,
            contact_id="99",
            handoff_requested=True,
        )

    def test_reminder_hook_chatwoot_only(self, monkeypatch):
        monkeypatch.setenv("CHATWOOT_BASE_URL", "https://chat.example.com")
        monkeypatch.setenv("CHATWOOT_AGENT_TOKEN", "tok")
        assert auto.labeling_reminder_hook(platform="chatwoot") is not None
        assert auto.labeling_reminder_hook(platform="cli") is None
