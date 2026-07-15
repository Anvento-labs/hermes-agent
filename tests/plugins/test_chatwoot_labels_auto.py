"""Tests for automatic Chatwoot conversation labeling."""

from unittest.mock import MagicMock, patch

import pytest

from plugins.platforms.chatwoot import labels_auto as auto


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


class TestClassifyConversationLabels:
    def test_find_gigs(self):
        labels = auto.classify_conversation_labels("what gigs are near me?")
        assert "gig-discovery" in labels

    def test_where_can_i_find_irl_gigs_is_app_help(self):
        """Navigation follow-up must not fall through to bare-gig → discovery."""
        labels = auto.classify_conversation_labels("where can i find irl gigs ?")
        assert labels == ["app-help"]
        assert "gig-discovery" not in labels

    def test_where_can_i_find_irl_gigs_after_app_sections(self):
        history = [
            {"role": "user", "content": "what section does the app contain ?"},
            {
                "role": "assistant",
                "content": "Home for your gigs, Explore to browse available ones.",
            },
        ]
        labels = auto.classify_conversation_labels(
            "where can i find irl gigs ?",
            conversation_history=history,
        )
        assert labels == ["app-help"]
        assert "gig-discovery" not in labels

    def test_where_is_explore_tab_is_app_help(self):
        labels = auto.classify_conversation_labels("where is the Explore tab?")
        assert labels == ["app-help"]

    def test_browse_while_enrolled_stays_discovery(self):
        with patch.object(
            auto,
            "_member_enrollment",
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
        assert "payment-payout" in labels
        assert "app-help" in labels

    def test_off_topic_fallback(self):
        labels = auto.classify_conversation_labels("hello there")
        assert labels == ["off-topic"]

    def test_hi_with_coach_welcome_not_payment(self):
        """Regression: welcome says 'get paid' must not label a bare hi."""
        welcome = (
            "Hey! I'm your CRWD Coach — here to help you finish gigs and get paid. "
            "What do you need?"
        )
        labels = auto.classify_conversation_labels(
            "hi",
            assistant_response=welcome,
        )
        assert labels == ["off-topic"]
        assert "payment-payout" not in labels
        assert "gig-discovery" not in labels

        result = auto.classify_conversation(
            "hi",
            assistant_response=welcome,
            allow_llm=True,
        )
        assert result.labels == ["off-topic"]
        assert result.confidence == "high"
        # Assistant welcome must not enter regex or LLM context.
        regex_text, llm_context = auto._build_turn_context("hi", (), welcome)
        assert "paid" not in regex_text
        assert "Assistant:" not in llm_context

    def test_who_are_u_not_gig_discovery_despite_coach_bio(self):
        bio = (
            "I'm your CRWD Coach — here to help you knock out your gigs and get paid. "
            "Ask me about finding gigs, next steps, proof submission, or anything else."
        )
        with patch.object(
            auto,
            "classify_with_auxiliary",
            return_value=["off-topic", "gig-discovery"],
        ) as llm:
            result = auto.classify_conversation(
                "who are u ?",
                assistant_response=bio,
                allow_llm=True,
                sticky_topics=["app-help"],
            )
        llm.assert_not_called()
        assert result.labels == ["off-topic"]
        assert "gig-discovery" not in result.labels
        assert result.confidence == "high"

    def test_where_online_gigs_in_app_is_app_help(self):
        labels = auto.classify_conversation_labels(
            "where can i find online gigs in the app"
        )
        assert labels == ["app-help"]
        assert "gig-discovery" not in labels

    def test_no_handoff_from_frustration_keywords(self):
        labels = auto.classify_conversation_labels("I'm so frustrated, get me a human")
        assert "handoff-escalation" not in labels

    def test_handoff_only_when_requested(self):
        with patch.object(
            auto,
            "_member_enrollment",
            return_value=(True, {"Some Gig"}),
        ):
            labels = auto.classify_conversation_labels(
                "my submission was rejected",
                handoff_requested=True,
                contact_id="contact-1",
            )
        assert "proof-submission" in labels
        assert "mid-gig-support" in labels
        assert "handoff-escalation" in labels

    def test_proof_without_enrollment(self):
        with patch.object(auto, "_member_enrollment", return_value=(False, set())):
            labels = auto.classify_conversation_labels(
                "how do I submit proof?",
                contact_id="contact-1",
            )
        assert labels == ["proof-submission"]

    def test_proof_with_enrollment(self):
        with patch.object(
            auto,
            "_member_enrollment",
            return_value=(True, {"Amazon Gig"}),
        ):
            labels = auto.classify_conversation_labels(
                "how do I submit proof?",
                contact_id="contact-1",
            )
        assert labels == ["proof-submission", "mid-gig-support"]

    def test_mid_gig_unenrolled_falls_back_to_discovery(self):
        with patch.object(auto, "_member_enrollment", return_value=(False, set())):
            labels = auto.classify_conversation_labels(
                "what's my deadline on the amazon gig?",
                contact_id="contact-1",
            )
        assert labels == ["gig-discovery"]

    def test_mid_gig_unknown_enrollment_skips_discovery_invention(self):
        with patch.object(auto, "_member_enrollment", return_value=None), patch.object(
            auto, "_llm_fallback_enabled", return_value=False,
        ):
            result = auto.classify_conversation(
                "what's my deadline on the amazon gig?",
                contact_id="contact-1",
                allow_llm=False,
            )
        assert "mid-gig-support" not in result.labels
        assert "gig-discovery" not in result.labels
        # With no tools/LLM/sticky, empty heuristic path collapses to off-topic.
        assert result.labels == ["off-topic"]

    def test_mid_gig_enrolled(self):
        with patch.object(
            auto,
            "_member_enrollment",
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
            "_member_enrollment",
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
            "_member_enrollment",
            return_value=(True, {"Target Gig"}),
        ):
            labels = auto.classify_conversation_labels(
                "what's my deadline on the amazon gig?",
                contact_id="contact-1",
            )
        assert labels == ["gig-discovery"]

    def test_unenrolled_gig_details_is_discovery(self):
        with patch.object(auto, "_member_enrollment", return_value=(False, set())):
            labels = auto.classify_conversation_labels(
                "give me details about the amazon gig ?",
                contact_id="contact-1",
            )
        assert labels == ["gig-discovery"]

    def test_opt_out_is_not_account_topic(self):
        labels = auto.classify_conversation_labels("stop texting me")
        assert "account-eligibility" not in labels
        assert "account-info" not in labels
        assert "scam" not in labels
        assert "handoff-escalation" not in labels
        assert labels == ["off-topic"]

    def test_opt_out_with_handoff_only(self):
        labels = auto.classify_conversation_labels(
            "stop texting me",
            handoff_requested=True,
        )
        assert labels == ["off-topic", "handoff-escalation"]
        assert "account-eligibility" not in labels

    def test_account_eligibility_only(self):
        labels = auto.classify_conversation_labels(
            "I'm not eligible in my state"
        )
        assert labels == ["account-eligibility"]
        assert "account-info" not in labels
        assert "scam" not in labels

    def test_account_info_ban(self):
        labels = auto.classify_conversation_labels("why was I banned?")
        assert labels == ["account-info"]
        assert "account-eligibility" not in labels

    def test_scam_phishing(self):
        labels = auto.classify_conversation_labels("this looks like phishing")
        assert labels == ["scam"]
        assert "account-eligibility" not in labels

    def test_eligibility_and_scam_both_apply(self):
        labels = auto.classify_conversation_labels(
            "I'm not eligible and this phishing email looks suspicious"
        )
        assert "account-eligibility" in labels
        assert "scam" in labels

    def test_no_label_cap_on_multi_intent_plus_handoff(self):
        labels = auto.classify_conversation_labels(
            "my payout page won't load, when will I get paid?",
            handoff_requested=True,
        )
        assert "payment-payout" in labels
        assert "app-help" in labels
        assert "handoff-escalation" in labels


class TestToolEvidenceLabels:
    def test_list_active_gigs_is_discovery(self):
        labels, reasons = auto.labels_from_tools(
            [{"tool": "crwd_db", "action": "list_active_gigs"}]
        )
        assert labels == ["gig-discovery"]
        assert any("list_active_gigs" in r for r in reasons)

    def test_waitlisted_is_mid_gig(self):
        labels, _ = auto.labels_from_tools(
            [{"tool": "crwd_db", "action": "get_waitlisted_gigs"}]
        )
        assert labels == ["mid-gig-support"]

    def test_receipts_is_proof(self):
        labels, _ = auto.labels_from_tools(
            [{"tool": "crwd_db", "action": "get_user_receipts"}]
        )
        assert labels == ["proof-submission"]

    def test_dot_is_payment(self):
        labels, _ = auto.labels_from_tools(
            [{"tool": "dot", "action": "get_user_transfers"}]
        )
        assert labels == ["payment-payout"]

    def test_get_gig_details_unenrolled_is_discovery(self):
        with patch.object(auto, "_member_enrollment", return_value=(False, set())):
            labels, _ = auto.labels_from_tools(
                [{"tool": "crwd_db", "action": "get_gig_details"}],
                contact_id="c1",
            )
        assert labels == ["gig-discovery"]

    def test_get_gig_details_with_enrolled_action_is_mid_gig(self):
        labels, _ = auto.labels_from_tools(
            [
                {"tool": "crwd_db", "action": "get_user_gigs"},
                {"tool": "crwd_db", "action": "get_gig_details"},
            ]
        )
        assert "mid-gig-support" in labels

    def test_get_user_alone_forces_no_label(self):
        labels, _ = auto.labels_from_tools(
            [{"tool": "crwd_db", "action": "get_user"}]
        )
        assert labels == []

    def test_classify_uses_tool_evidence(self):
        labels = auto.classify_conversation_labels(
            "ok",
            tool_evidence=[{"tool": "crwd_db", "action": "list_active_gigs"}],
        )
        assert labels == ["gig-discovery"]


class TestTopicSwitchAndSticky:
    def test_prior_app_help_does_not_re_fire_on_discovery(self):
        history = [
            {"role": "user", "content": "where is the Explore tab?"},
            {"role": "assistant", "content": "Open Explore from the bottom nav."},
        ]
        labels = auto.classify_conversation_labels(
            "what gigs are near me?",
            conversation_history=history,
        )
        assert labels == ["gig-discovery"]
        assert "app-help" not in labels

    def test_high_conf_replace_drops_previous_topic(self, monkeypatch):
        monkeypatch.setenv("CHATWOOT_BASE_URL", "https://chat.example.com")
        monkeypatch.setenv("CHATWOOT_AGENT_TOKEN", "agent-tok")
        monkeypatch.setenv("CHATWOOT_ACCOUNT_ID", "1")
        assigned = []

        def _capture(account_id, conversation_id, labels, replace):
            assigned.append(list(labels))
            return {"success": True, "labels": labels, "error": None}

        with patch.object(auto, "_resolve_conversation", return_value=("1", "42")), patch.object(
            auto, "_create_labels_if_not_exists",
            return_value={"success": True, "existing": ["app-help"]},
        ), patch.object(auto, "_assign_labels", side_effect=_capture), patch.object(
            auto, "_member_enrollment", return_value=(False, set()),
        ), patch.object(auto, "_llm_fallback_enabled", return_value=False):
            auto.auto_label_conversation("where is the Explore tab?")
            auto.auto_label_conversation(
                "what gigs are near me?",
                tool_evidence=[{"tool": "crwd_db", "action": "list_active_gigs"}],
            )

        assert assigned[0] == ["app-help"]
        assert assigned[1] == ["gig-discovery"]
        assert "app-help" not in assigned[1]

    def test_low_conf_sticky_keeps_previous(self, monkeypatch):
        monkeypatch.setenv("CHATWOOT_BASE_URL", "https://chat.example.com")
        monkeypatch.setenv("CHATWOOT_AGENT_TOKEN", "agent-tok")
        monkeypatch.setenv("CHATWOOT_ACCOUNT_ID", "1")
        assigned = []

        def _capture(account_id, conversation_id, labels, replace):
            assigned.append(list(labels))
            return {"success": True, "labels": labels, "error": None}

        with patch.object(auto, "_resolve_conversation", return_value=("1", "42")), patch.object(
            auto, "_create_labels_if_not_exists",
            return_value={"success": True, "existing": ["app-help"]},
        ), patch.object(auto, "_assign_labels", side_effect=_capture), patch.object(
            auto, "_llm_fallback_enabled", return_value=False,
        ):
            auto.auto_label_conversation("where is the Explore tab?")
            auto.auto_label_conversation("ok")

        assert assigned[0] == ["app-help"]
        assert assigned[1] == ["app-help"]

    def test_sticky_beats_llm_on_ambiguous_followup(self, monkeypatch):
        """Low-conf turn with prior topic must not let LLM invent gig-discovery."""
        monkeypatch.setenv("CHATWOOT_BASE_URL", "https://chat.example.com")
        monkeypatch.setenv("CHATWOOT_AGENT_TOKEN", "agent-tok")
        monkeypatch.setenv("CHATWOOT_ACCOUNT_ID", "1")
        assigned = []

        def _capture(account_id, conversation_id, labels, replace):
            assigned.append(list(labels))
            return {"success": True, "labels": labels, "error": None}

        with patch.object(auto, "_resolve_conversation", return_value=("1", "42")), patch.object(
            auto, "_create_labels_if_not_exists",
            return_value={"success": True, "existing": ["app-help"]},
        ), patch.object(auto, "_assign_labels", side_effect=_capture), patch.object(
            auto, "_llm_fallback_enabled", return_value=True,
        ), patch.object(
            auto,
            "classify_with_auxiliary",
            return_value=["off-topic", "gig-discovery"],
        ) as llm:
            auto.auto_label_conversation("where is the Explore tab?")
            auto.auto_label_conversation("ok")

        assert assigned[0] == ["app-help"]
        assert assigned[1] == ["app-help"]
        # Sticky wins — LLM must not run when prior topics exist.
        llm.assert_not_called()


class TestHandoffDetection:
    def test_handoff_tool_hook_sets_flag(self):
        auto.reset_handoff_flag()
        auto.record_tool_evidence_hook(platform="chatwoot", tool_name="crwd_handoff")
        assert auto.handoff_requested_this_turn() is True

    def test_record_tool_stores_crwd_db_action(self):
        auto.reset_tool_evidence()
        auto.record_tool_evidence_hook(
            tool_name="crwd_db",
            args={"action": "list_active_gigs"},
        )
        evidence = auto.tool_evidence_this_turn()
        assert evidence == ({"tool": "crwd_db", "action": "list_active_gigs"},)

    def test_handoff_tool_hook_alias_still_works(self):
        auto.reset_handoff_flag()
        auto.handoff_tool_hook(platform="chatwoot", tool_name="crwd_db")
        assert auto.handoff_requested_this_turn() is False
        assert auto.tool_evidence_this_turn()

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


class TestAuxiliaryFallback:
    def test_classify_with_auxiliary_parses_json(self):
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=MagicMock(content='{"labels": ["app-help"]}'))]
        with patch("agent.auxiliary_client.call_llm", return_value=mock_resp) as call_llm:
            assert auto.classify_with_auxiliary("Member: where is home?") == ["app-help"]
        call_llm.assert_called_once()
        kwargs = call_llm.call_args.kwargs
        assert "tools" not in kwargs
        assert "tool_choice" not in kwargs
        system = call_llm.call_args.kwargs["messages"][0]["content"]
        assert "account-info" in system
        assert "scam" in system
        assert "account-eligibility" in system
        assert "opt-out" in system.lower() or "stop-contact" in system.lower()

    def test_low_conf_uses_llm_when_enabled(self):
        with patch.object(auto, "_llm_fallback_enabled", return_value=True), patch.object(
            auto, "classify_with_auxiliary", return_value=["app-help"]
        ) as llm:
            result = auto.classify_conversation(
                "hmm",
                allow_llm=True,
                sticky_topics=None,
            )
        llm.assert_called_once()
        assert result.labels == ["app-help"]
        assert result.source in {"llm", "mixed"}

    def test_llm_ungrounded_gig_discovery_dropped(self):
        with patch.object(auto, "_llm_fallback_enabled", return_value=True), patch.object(
            auto,
            "classify_with_auxiliary",
            return_value=["off-topic", "gig-discovery"],
        ):
            result = auto.classify_conversation(
                "hmm",
                allow_llm=True,
                sticky_topics=None,
            )
        assert "gig-discovery" not in result.labels
        assert result.labels == ["off-topic"]

    def test_llm_ungrounded_scam_dropped(self):
        with patch.object(auto, "_llm_fallback_enabled", return_value=True), patch.object(
            auto,
            "classify_with_auxiliary",
            return_value=["off-topic", "scam"],
        ):
            result = auto.classify_conversation(
                "hmm",
                allow_llm=True,
                sticky_topics=None,
            )
        assert "scam" not in result.labels
        assert result.labels == ["off-topic"]


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
            auto, "_create_labels_if_not_exists",
            return_value={"success": True, "existing": ["proof-submission"]},
        ), patch.object(
            auto, "_member_enrollment", return_value=(False, set()),
        ), patch.object(
            auto, "_assign_labels",
            return_value={"success": True, "labels": ["proof-submission"], "error": None},
        ) as assign:
            out = auto.auto_label_conversation("how do I submit proof?")
        assert out["success"] is True
        assert out["classified"] == ["proof-submission"]
        assign.assert_called_once_with("1", "42", ["proof-submission"], replace=True)
        assert "confidence" in out
        assert "reasons" in out


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

    def test_passes_handoff_flag_and_assistant(self):
        auto.reset_handoff_flag()
        auto.reset_contact_id()
        auto.reset_tool_evidence()
        auto._contact_id_this_turn.set("99")
        auto.record_tool_evidence_hook(tool_name="crwd_handoff", args={})
        with patch.object(auto, "auto_label_conversation") as fn:
            auto.auto_label_hook(
                platform="chatwoot",
                user_message="stop texting me",
                assistant_response="I'll loop in a human.",
            )
        fn.assert_called_once()
        kwargs = fn.call_args.kwargs
        assert kwargs["user_message"] == "stop texting me"
        assert kwargs["contact_id"] == "99"
        assert kwargs["handoff_requested"] is True
        assert kwargs["assistant_response"] == "I'll loop in a human."
        assert kwargs["tool_evidence"]

    def test_reminder_hook_chatwoot_only(self, monkeypatch):
        monkeypatch.setenv("CHATWOOT_BASE_URL", "https://chat.example.com")
        monkeypatch.setenv("CHATWOOT_AGENT_TOKEN", "tok")
        assert auto.labeling_reminder_hook(platform="chatwoot") is not None
        assert auto.labeling_reminder_hook(platform="cli") is None
        # Reminder resets tool evidence
        auto.record_tool_evidence_hook(tool_name="dot", args={"action": "get_transfer"})
        auto.labeling_reminder_hook(platform="cli")
        assert auto.tool_evidence_this_turn() == ()
