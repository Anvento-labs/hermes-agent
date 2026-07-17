"""Tests for automatic Chatwoot conversation labeling (applied taxonomy)."""

from unittest.mock import patch

import pytest

from plugins.platforms.chatwoot import labels_auto as auto
from plugins.platforms.chatwoot.labels import (
    APPLIED_LABEL_TITLES,
    UNAPPLIED_LABEL_TITLES,
)


@pytest.fixture(autouse=True)
def _reset_label_state():
    auto.reset_handoff_flag()
    auto.reset_contact_id()
    auto.reset_tool_evidence()
    auto.clear_sticky_labels_for_tests()
    yield
    auto.reset_handoff_flag()
    auto.reset_contact_id()
    auto.reset_tool_evidence()
    auto.clear_sticky_labels_for_tests()


@pytest.fixture(autouse=True)
def _no_new_user_by_default(monkeypatch):
    """Most tests are about intent/tools — keep new-user off unless asserted."""
    monkeypatch.setattr(auto, "_member_has_completed_gig", lambda _cid: True)


class TestTaxonomySets:
    def test_applied_and_unapplied_disjoint(self):
        assert APPLIED_LABEL_TITLES.isdisjoint(UNAPPLIED_LABEL_TITLES)

    def test_new_titles_are_applied(self):
        for title in (
            "payment-issue",
            "proof-acceptance",
            "proof-rejection",
            "new-user",
        ):
            assert title in APPLIED_LABEL_TITLES
            assert title not in UNAPPLIED_LABEL_TITLES

    def test_unapplied_includes_former_topics(self):
        for title in (
            "mid-gig-support",
            "proof-submission",
            "gig-discovery",
            "general-inquiry",
            "payment-payout",
            "account-eligibility",
            "account-info",
            "scam",
            "off-topic",
        ):
            assert title in UNAPPLIED_LABEL_TITLES


class TestAppliedIntentLabels:
    def test_payment_issue(self):
        labels = auto.classify_conversation_labels("did I get paid yet?")
        assert labels == ["payment-issue"]

    def test_multi_label_payment_and_app_help(self):
        labels = auto.classify_conversation_labels(
            "my payout page won't load, when will I get paid?"
        )
        assert "payment-issue" in labels
        assert "app-help" in labels

    def test_app_help(self):
        labels = auto.classify_conversation_labels("where is the Explore tab?")
        assert labels == ["app-help"]

    def test_where_can_i_find_irl_gigs_is_app_help(self):
        labels = auto.classify_conversation_labels("where can i find irl gigs ?")
        assert labels == ["app-help"]
        assert "gig-discovery" not in labels


class TestUnappliedNotAssigned:
    def test_gig_discovery_intent_emits_no_topic(self):
        labels = auto.classify_conversation_labels("what gigs are near me?")
        assert "gig-discovery" not in labels
        assert labels == []

    def test_general_inquiry_emits_no_topic(self):
        labels = auto.classify_conversation_labels("what is crwd?")
        assert "general-inquiry" not in labels
        assert labels == []

    def test_scam_emits_no_topic(self):
        labels = auto.classify_conversation_labels("this looks like phishing")
        assert "scam" not in labels
        assert "scam" not in labels

    def test_proof_question_emits_no_proof_submission(self):
        labels = auto.classify_conversation_labels("how do I submit proof?")
        assert "proof-submission" not in labels
        assert "mid-gig-support" not in labels

    def test_account_info_emits_no_topic(self):
        labels = auto.classify_conversation_labels("what is my name?")
        assert "account-info" not in labels
        assert labels == []

    def test_eligibility_emits_no_topic(self):
        labels = auto.classify_conversation_labels("I am not eligible")
        assert "account-eligibility" not in labels
        assert labels == []


class TestGatesNoTopic:
    def test_greeting_is_no_topic(self):
        labels = auto.classify_conversation_labels("hello there")
        assert labels == []

    def test_hi_with_coach_welcome_not_payment(self):
        welcome = (
            "Hey! I'm your CRWD Coach — here to help you finish gigs and get paid. "
            "What do you need?"
        )
        labels = auto.classify_conversation_labels("hi", assistant_response=welcome)
        assert labels == []
        assert "payment-issue" not in labels

    def test_who_are_you_is_no_topic(self):
        labels = auto.classify_conversation_labels("Who are you?")
        assert labels == []

    def test_empty_is_no_topic(self):
        result = auto.classify_conversation(user_message="")
        assert result.labels == []
        assert "gate:empty->no-topic" in result.reasons


class TestHandoffAndProofVerdicts:
    def test_handoff_only_when_requested(self):
        labels = auto.classify_conversation_labels("I am frustrated", handoff_requested=True)
        assert "handoff-escalation" in labels

    def test_no_handoff_without_tool(self):
        labels = auto.classify_conversation_labels("I am frustrated")
        assert "handoff-escalation" not in labels

    def test_proof_acceptance_from_store_proof(self):
        evidence = [
            {
                "tool": "crwd_db",
                "action": "store_proof",
                "proof_status": "accepted",
                "is_gig_completed": "false",
            },
            {
                "tool": "crwd_db",
                "action": "store_proof",
                "proof_status": "accepted",
            },
        ]
        labels = auto.classify_conversation_labels(
            "here is my receipt",
            tool_evidence=evidence,
        )
        assert "proof-acceptance" in labels
        assert "proof-rejection" not in labels

    def test_proof_rejection_if_any_rejected(self):
        evidence = [
            {"tool": "crwd_db", "action": "store_proof", "proof_status": "accepted"},
            {"tool": "crwd_db", "action": "store_proof", "proof_status": "rejected"},
        ]
        labels = auto.classify_conversation_labels(
            "here is my receipt",
            tool_evidence=evidence,
        )
        assert labels.count("proof-rejection") == 1 or "proof-rejection" in labels
        assert "proof-acceptance" not in labels

    def test_record_tool_evidence_parses_store_proof_result(self):
        auto.record_tool_evidence_hook(
            tool_name="crwd_db",
            args={"action": "store_proof", "status": "accepted"},
            result='{"_type":"crwd_proof_stored","status":"rejected","is_gig_completed":false}',
        )
        evidence = auto.tool_evidence_this_turn()
        assert evidence[-1]["proof_status"] == "rejected"


class TestNewUser:
    def test_new_user_when_no_completed_gig(self, monkeypatch):
        monkeypatch.setattr(auto, "_member_has_completed_gig", lambda _cid: False)
        labels = auto.classify_conversation_labels(
            "did I get paid yet?",
            contact_id="c1",
        )
        assert "payment-issue" in labels
        assert "new-user" in labels

    def test_no_new_user_when_completed(self, monkeypatch):
        monkeypatch.setattr(auto, "_member_has_completed_gig", lambda _cid: True)
        labels = auto.classify_conversation_labels(
            "did I get paid yet?",
            contact_id="c1",
        )
        assert "new-user" not in labels

    def test_unknown_completed_skips_new_user(self, monkeypatch):
        monkeypatch.setattr(auto, "_member_has_completed_gig", lambda _cid: None)
        labels = auto.classify_conversation_labels(
            "did I get paid yet?",
            contact_id="c1",
        )
        assert "new-user" not in labels

    def test_this_turn_gig_complete_clears_new_user(self, monkeypatch):
        monkeypatch.setattr(auto, "_member_has_completed_gig", lambda _cid: False)
        evidence = [
            {
                "tool": "crwd_db",
                "action": "store_proof",
                "proof_status": "accepted",
                "is_gig_completed": "true",
            },
        ]
        labels = auto.classify_conversation_labels(
            "here is my last proof",
            contact_id="c1",
            tool_evidence=evidence,
        )
        assert "new-user" not in labels
        assert "proof-acceptance" in labels


class TestDialogueActMapping:
    def test_payout_maps_to_payment_issue(self):
        labels = auto.acts_to_labels(["payout"], "when paid?", "")
        assert labels == ["payment-issue"]

    def test_app_nav_maps_to_app_help(self):
        labels = auto.acts_to_labels(["app_nav"], "where is explore?", "")
        assert labels == ["app-help"]

    def test_unapplied_acts_emit_nothing(self):
        labels = auto.acts_to_labels(
            ["browse_open_gigs", "general_inquiry", "scam", "proof", "chitchat"],
            "whatever",
            "",
        )
        assert labels == []


class TestStickyAppliedOnly:
    def test_sticky_keeps_payment_issue(self):
        result = auto.classify_conversation(
            "ok",
            allow_llm=False,
            sticky_topics=["payment-issue"],
            sticky_acts=["payout"],
        )
        assert "payment-issue" in result.labels

    def test_sticky_ignores_unapplied_topics(self):
        result = auto.classify_conversation(
            "ok",
            allow_llm=False,
            sticky_topics=["gig-discovery", "off-topic"],
            sticky_acts=["browse_open_gigs"],
        )
        assert "gig-discovery" not in result.labels
        assert "off-topic" not in result.labels


class TestAuxiliaryActClassify:
    def test_act_classify_uses_plain_json_not_tools(self):
        mock_resp = type("R", (), {})()
        mock_resp.choices = [
            type("C", (), {"message": type("M", (), {"content": '{"acts":["app_nav"],"primary":"app_nav","confidence":"high","reasons":[]}'})()})()
        ]
        with patch("agent.auxiliary_client.call_llm", return_value=mock_resp) as call_llm:
            result = auto.classify_acts_with_auxiliary("Member 1: where is home?")
        assert result is not None
        assert result["acts"] == ["app_nav"]
        kwargs = call_llm.call_args.kwargs
        assert "tools" not in kwargs
        assert "tool_choice" not in kwargs


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

    def test_applies_payment_issue(self, chatwoot_env):
        with patch.object(auto, "_resolve_conversation", return_value=("1", "42")), patch.object(
            auto, "_preserved_labels", return_value=[],
        ), patch.object(
            auto, "_create_labels_if_not_exists",
            return_value={"success": True, "existing": ["payment-issue"]},
        ), patch.object(
            auto, "_llm_fallback_enabled", return_value=False,
        ), patch.object(
            auto, "_assign_labels",
            return_value={"success": True, "labels": ["payment-issue"], "error": None},
        ) as assign:
            out = auto.auto_label_conversation("when will I get paid?")
        assert out["success"] is True
        assert "payment-issue" in out["classified"]
        assign.assert_called_once()
        assert assign.call_args[0][2] == out["classified"]
        assert assign.call_args[1]["replace"] is True

    def test_sticky_handoff_preserved_on_later_turn(self, chatwoot_env):
        with patch.object(auto, "_resolve_conversation", return_value=("1", "42")), patch.object(
            auto, "_preserved_labels", return_value=["handoff-escalation"],
        ), patch.object(
            auto, "_create_labels_if_not_exists",
            return_value={"success": True, "existing": ["payment-issue"]},
        ), patch.object(
            auto, "_llm_fallback_enabled", return_value=False,
        ), patch.object(
            auto, "_assign_labels",
            return_value={"success": True, "labels": [], "error": None},
        ) as assign:
            out = auto.auto_label_conversation("ok thank you", handoff_requested=False)
        assert "handoff-escalation" in out["classified"]
        assert "handoff-escalation" in assign.call_args[0][2]

    def test_risk_band_survives_a_later_turn(self, chatwoot_env):
        with patch.object(auto, "_resolve_conversation", return_value=("1", "42")), patch.object(
            auto, "_preserved_labels", return_value=["risk-high"],
        ), patch.object(
            auto, "_create_labels_if_not_exists",
            return_value={"success": True, "existing": []},
        ), patch.object(
            auto, "_llm_fallback_enabled", return_value=False,
        ), patch.object(
            auto, "_assign_labels",
            return_value={"success": True, "labels": [], "error": None},
        ) as assign:
            auto.auto_label_conversation("ok thanks", handoff_requested=False)
        assert "risk-high" in assign.call_args[0][2]

    def test_gig_complete_survives_a_later_turn(self, chatwoot_env):
        with patch.object(auto, "_resolve_conversation", return_value=("1", "42")), patch.object(
            auto, "_preserved_labels", return_value=["gig-complete"],
        ), patch.object(
            auto, "_create_labels_if_not_exists",
            return_value={"success": True, "existing": []},
        ), patch.object(
            auto, "_llm_fallback_enabled", return_value=False,
        ), patch.object(
            auto, "_assign_labels",
            return_value={"success": True, "labels": [], "error": None},
        ) as assign:
            auto.auto_label_conversation("thanks", handoff_requested=False)
        assert "gig-complete" in assign.call_args[0][2]

    def test_preserved_labels_stay_out_of_sticky_topic_memory(self, chatwoot_env):
        with patch.object(auto, "_resolve_conversation", return_value=("1", "42")), patch.object(
            auto, "_preserved_labels", return_value=["risk-high", "gig-complete"],
        ), patch.object(
            auto, "_create_labels_if_not_exists",
            return_value={"success": True, "existing": []},
        ), patch.object(
            auto, "_llm_fallback_enabled", return_value=False,
        ), patch.object(
            auto, "_assign_labels",
            return_value={"success": True, "labels": ["payment-issue"], "error": None},
        ):
            auto.auto_label_conversation("when will I get paid?")
        stored = auto._get_sticky_topics("1", "42")
        assert "risk-high" not in stored
        assert "gig-complete" not in stored
        assert "payment-issue" in stored


class TestPreservedLabelsHelper:
    def test_only_handoff_gig_complete_and_risk(self):
        payload = {
            "payload": [
                "payment-issue",
                "handoff-escalation",
                "risk-high",
                "gig-complete",
                "off-topic",
            ]
        }
        with patch(
            "plugins.platforms.chatwoot.labels_tool._api_request",
            return_value=(True, payload, ""),
        ):
            out = auto._preserved_labels("1", "42")
        assert sorted(out) == ["gig-complete", "handoff-escalation", "risk-high"]
