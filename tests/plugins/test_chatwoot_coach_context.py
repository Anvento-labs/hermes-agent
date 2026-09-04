"""Unit tests for the CRWD Coach context injector.

Covers the member-id resolver (contact-attr hit, Mongo fallback, cache, platform
gate) and the pre_llm_call hook, with the Chatwoot HTTP GET and Mongo lookup
mocked. No live Chatwoot/Mongo is touched.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from plugins.platforms.chatwoot import coach_context as cc


@pytest.fixture(autouse=True)
def _clear_cache():
    cc._reset_cache()
    cc.reset_cross_user_request()
    cc.reset_webhook_conversation_status()
    yield
    cc._reset_cache()
    cc.reset_cross_user_request()
    cc.reset_webhook_conversation_status()


@pytest.fixture
def chatwoot_env(monkeypatch):
    monkeypatch.setenv("CHATWOOT_BASE_URL", "https://chat.example.com")
    monkeypatch.setenv("CHATWOOT_TOKEN", "bot-tok")
    monkeypatch.setenv("CRWD_MONGO_URI", "mongodb://localhost:27017/")
    # Session context: chatwoot conversation "7:42".
    monkeypatch.setattr(
        "gateway.session_context.get_session_env",
        lambda name, default="": {
            "HERMES_SESSION_PLATFORM": "chatwoot",
            "HERMES_SESSION_CHAT_ID": "7:42",
        }.get(name, default),
    )


class TestResolve:
    def test_returns_id_from_contact_attribute(self, chatwoot_env):
        contact = {"custom_attributes": {"joincrwd_user_id": "abc123"}}
        with patch.object(cc, "_get_contact", return_value=contact) as g:
            assert cc.resolve_member_crwd_id("55") == "abc123"
            g.assert_called_once_with("7", "55")

    def test_falls_back_to_mongo_when_attr_missing(self, chatwoot_env):
        contact = {"custom_attributes": {}, "email": "m@x.com", "phone_number": "+1555"}
        with patch.object(cc, "_get_contact", return_value=contact), patch(
            "plugins.platforms.chatwoot.enrichment.fetch_user",
            return_value={"_id": "deadbeef"},
        ) as fu:
            assert cc.resolve_member_crwd_id("55") == "deadbeef"
            fu.assert_called_once_with("m@x.com", "+1555")

    def test_none_when_no_contact_and_no_match(self, chatwoot_env):
        with patch.object(cc, "_get_contact", return_value=None):
            assert cc.resolve_member_crwd_id("55") is None

    def test_caches_result_no_second_http(self, chatwoot_env):
        contact = {"custom_attributes": {"joincrwd_user_id": "abc123"}}
        with patch.object(cc, "_get_contact", return_value=contact) as g:
            assert cc.resolve_member_crwd_id("55") == "abc123"
            assert cc.resolve_member_crwd_id("55") == "abc123"
            g.assert_called_once()

    def test_blank_contact_id_returns_none(self, chatwoot_env):
        assert cc.resolve_member_crwd_id("") is None


class TestLocationHelpers:
    def test_format_profile_location_order(self):
        assert cc._format_profile_location({
            "city": "Austin",
            "state": "TX",
            "postal_code": "78701",
            "country": "US",
        }) == "Austin, TX, 78701, US"

    def test_fetch_member_location_maps_profile(self, chatwoot_env):
        with patch(
            "tools.crwd_db_tool.fetch_user_profile",
            return_value={
                "success": True,
                "user": {
                    "city": "Austin",
                    "state": "TX",
                    "postal_code": "78701",
                    "country": "US",
                    "email": "a@b.com",
                },
            },
        ):
            loc = cc._fetch_member_location("abc123")
        assert loc == {
            "city": "Austin",
            "state": "TX",
            "country": "US",
            "postal_code": "78701",
        }
        # Cached — second call does not re-hit the profile helper.
        with patch(
            "tools.crwd_db_tool.fetch_user_profile",
            side_effect=AssertionError("should use cache"),
        ):
            assert cc._fetch_member_location("abc123") == loc

    def test_fetch_member_location_empty_when_no_fields(self, chatwoot_env):
        with patch(
            "tools.crwd_db_tool.fetch_user_profile",
            return_value={"success": True, "user": {"email": "a@b.com"}},
        ):
            assert cc._fetch_member_location("abc123") == {}

    def test_fetch_member_location_maps_dob_and_gender(self, chatwoot_env):
        with patch(
            "tools.crwd_db_tool.fetch_user_profile",
            return_value={
                "success": True,
                "user": {
                    "city": "Austin",
                    "dob": "12/20/1998",
                    "gender": "female",
                    "email": "a@b.com",
                },
            },
        ):
            loc = cc._fetch_member_location("abc123")
        assert loc == {
            "city": "Austin",
            "dob": "1998-12-20",
            "gender": "female",
        }

    def test_fetch_member_location_omits_unparseable_dob(self, chatwoot_env):
        with patch(
            "tools.crwd_db_tool.fetch_user_profile",
            return_value={
                "success": True,
                "user": {"dob": "not-a-date", "gender": "  "},
            },
        ):
            assert cc._fetch_member_location("abc123") == {}

    def test_location_from_profile_strips_dob_gender(self):
        assert cc._location_from_profile({
            "city": "Austin",
            "dob": "1998-12-20",
            "gender": "female",
        }) == {"city": "Austin"}


class TestHook:
    def test_injects_context_when_resolved(self, chatwoot_env):
        with patch.object(cc, "resolve_member_crwd_id", return_value="abc123"), patch.object(
            cc, "_fetch_member_location", return_value=None
        ):
            out = cc.member_context_hook(platform="chatwoot", sender_id="55")
        assert out is not None
        assert "abc123" in out["context"]
        assert "Authenticated user_id" in out["context"]
        assert "Never look up a different member" in out["context"]
        assert "list_active_gigs" in out["context"]
        assert "get_user_gig_status" in out["context"]
        assert "clarify" in out["context"]
        assert "plain language" in out["context"]
        assert "never say turn" in out["context"]
        assert "Gig scope routing" not in out["context"]
        assert "AMBIGUOUS" not in out["context"]
        assert "MUST" not in out["context"]
        assert "mandatory" not in out["context"].lower()

    def test_injects_profile_location_when_present(self, chatwoot_env):
        loc = {
            "city": "Austin",
            "state": "TX",
            "postal_code": "78701",
            "country": "US",
        }
        with patch.object(cc, "resolve_member_crwd_id", return_value="abc123"), patch.object(
            cc, "_fetch_member_location", return_value=loc
        ):
            out = cc.member_context_hook(platform="chatwoot", sender_id="55")
        assert out is not None
        assert "Profile location" in out["context"]
        assert "Austin, TX, 78701, US" in out["context"]
        assert "ignore Honcho" in out["context"]
        assert "Sacramento" not in out["context"]

    def test_injects_ask_when_profile_has_no_location(self, chatwoot_env):
        with patch.object(cc, "resolve_member_crwd_id", return_value="abc123"), patch.object(
            cc, "_fetch_member_location", return_value={}
        ):
            out = cc.member_context_hook(platform="chatwoot", sender_id="55")
        assert out is not None
        assert "no city/ZIP on file" in out["context"]
        assert "Do not guess from Honcho" in out["context"]
        assert "Profile date of birth" not in out["context"]
        assert "Profile gender" not in out["context"]

    def test_injects_dob_age_and_gender_when_present(self, chatwoot_env):
        from datetime import date

        from tools.crwd_db.serialize import _age_from_dob

        dob = "1998-12-20"
        age = _age_from_dob(dob, today=date.today())
        profile = {
            "city": "Austin",
            "state": "TX",
            "postal_code": "78701",
            "country": "US",
            "dob": dob,
            "gender": "female",
        }
        with patch.object(cc, "resolve_member_crwd_id", return_value="abc123"), patch.object(
            cc, "_fetch_member_location", return_value=profile
        ):
            out = cc.member_context_hook(platform="chatwoot", sender_id="55")
        assert out is not None
        assert f"Profile date of birth (YYYY-MM-DD): {dob} (age {age})." in out["context"]
        assert "Profile gender: female." in out["context"]
        assert "Austin, TX, 78701, US" in out["context"]

    def test_injects_dob_without_age_when_uncomputable(self, chatwoot_env):
        with patch.object(cc, "resolve_member_crwd_id", return_value="abc123"), patch.object(
            cc, "_fetch_member_location", return_value={"dob": "1998-12-20"}
        ), patch("tools.crwd_db.serialize._age_from_dob", return_value=None):
            out = cc.member_context_hook(platform="chatwoot", sender_id="55")
        assert out is not None
        assert "Profile date of birth (YYYY-MM-DD): 1998-12-20." in out["context"]
        assert "(age " not in out["context"]
        assert "no city/ZIP on file" in out["context"]
        assert "Profile gender" not in out["context"]

    def test_none_off_chatwoot(self, chatwoot_env):
        with patch.object(cc, "_is_chatwoot", return_value=False):
            assert cc.member_context_hook(platform="telegram", sender_id="55") is None

    def test_none_without_mongo_uri(self, chatwoot_env, monkeypatch):
        monkeypatch.delenv("CRWD_MONGO_URI", raising=False)
        assert cc.member_context_hook(platform="chatwoot", sender_id="55") is None

    def test_none_without_sender_id(self, chatwoot_env):
        assert cc.member_context_hook(platform="chatwoot", sender_id="") is None

    def test_none_when_unresolved(self, chatwoot_env):
        with patch.object(cc, "resolve_member_crwd_id", return_value=None):
            assert cc.member_context_hook(platform="chatwoot", sender_id="55") is None

    def test_hook_never_raises(self, chatwoot_env):
        with patch.object(cc, "resolve_member_crwd_id", side_effect=RuntimeError("boom")):
            assert cc.member_context_hook(platform="chatwoot", sender_id="55") is None

    def test_cross_user_message_detection(self, chatwoot_env):
        member_id = "6a33bb6003b1c0cc31a7baa5"
        foreign_id = "69a6f191cb29b0b371b3a156"
        assert cc.message_requests_other_member(
            f"in which gigs has the user {foreign_id} enrolled in?",
            member_id,
        )
        assert not cc.message_requests_other_member(
            f"tell me about gig {foreign_id}",
            member_id,
        )
        assert not cc.message_requests_other_member(
            f"what gigs am I in, user {member_id}?",
            member_id,
        )

    def test_privacy_ask_foreign_oid_without_user_prefix(self, chatwoot_env):
        member_id = "6a33bb6003b1c0cc31a7baa5"
        foreign_id = "69a6f191cb29b0b371b3a156"
        assert cc.message_requests_other_member(
            f"what is the name of {foreign_id}?",
            member_id,
        )
        assert cc.message_requests_other_member(
            f"what gigs is {foreign_id} part of?",
            member_id,
        )
        assert not cc.message_requests_other_member(
            f"what is the name of {member_id}?",
            member_id,
        )

    def test_another_person_data_without_oid(self, chatwoot_env):
        member_id = "6a33bb6003b1c0cc31a7baa5"
        assert cc.message_requests_other_member(
            "what gigs is another user enrolled in?",
            member_id,
        )
        assert cc.message_requests_other_member(
            "show me someone else's account",
            member_id,
        )
        assert cc.message_requests_other_member(
            "what are their gigs?",
            member_id,
        )

    def test_participant_list_and_third_party_pii(self, chatwoot_env):
        member_id = "6a33bb6003b1c0cc31a7baa5"
        assert cc.message_requests_unauthorized_info(
            "list participant of crown of glory"
        ) == (True, "participant_list")
        assert cc.message_requests_other_member(
            "list participant of crown of glory",
            member_id,
        )
        assert cc.message_requests_unauthorized_info(
            "i met Alice at Crown of Glory. kindly provide his number"
        ) == (True, "third_party_pii")
        assert not cc.message_requests_other_member(
            "details about crown of glory",
            member_id,
        )
        matched, kind = cc.message_requests_unauthorized_info("what is my phone number")
        assert matched is False
        assert kind == ""


class TestStatusHandoffGuard:
    def test_resolved_injects_no_claim_rule_with_member(self, chatwoot_env):
        cc.bind_webhook_conversation_status("resolved")
        with patch.object(cc, "resolve_member_crwd_id", return_value="abc123"), patch.object(
            cc, "_fetch_member_location", return_value=None
        ):
            out = cc.member_context_hook(platform="chatwoot", sender_id="55")
        assert out is not None
        assert "status: resolved" in out["context"]
        assert "prior handoff is closed" in out["context"]
        assert "history only" in out["context"]
        assert "opened: true this turn" in out["context"]
        assert "CRWD Coach" in out["context"]
        assert "Authenticated user_id: abc123" in out["context"]

    def test_resolved_injects_rule_without_member_id(self, chatwoot_env):
        cc.bind_webhook_conversation_status("resolved")
        with patch.object(cc, "resolve_member_crwd_id", return_value=None):
            out = cc.member_context_hook(platform="chatwoot", sender_id="55")
        assert out is not None
        assert "status: resolved" in out["context"]
        assert "prior handoff is closed" in out["context"]

    def test_open_status_skips_claim_block(self, chatwoot_env):
        cc.bind_webhook_conversation_status("open")
        with patch.object(cc, "resolve_member_crwd_id", return_value="abc123"), patch.object(
            cc, "_fetch_member_location", return_value=None
        ):
            out = cc.member_context_hook(platform="chatwoot", sender_id="55")
        assert out is not None
        assert "prior handoff is closed" not in out["context"]
        assert "status: open" not in out["context"]

    def test_missing_status_skips_claim_block(self, chatwoot_env):
        with patch.object(cc, "resolve_member_crwd_id", return_value="abc123"), patch.object(
            cc, "_fetch_member_location", return_value=None
        ):
            out = cc.member_context_hook(platform="chatwoot", sender_id="55")
        assert out is not None
        assert "prior handoff is closed" not in out["context"]

    def test_bind_normalizes_status(self):
        cc.bind_webhook_conversation_status("  Resolved ")
        assert cc.webhook_conversation_status() == "resolved"

    def test_injects_campaign_code_miss_copy(self, chatwoot_env):
        miss = '{"_type":"campaign_code_match","query":"FRGP","items":[],"error":null}'
        with patch.object(cc, "resolve_member_crwd_id", return_value="abc123"), patch.object(
            cc, "_fetch_member_location", return_value=None
        ), patch(
            "tools.crwd_db.gigs._lookup_campaign_code", return_value=miss
        ):
            out = cc.member_context_hook(
                platform="chatwoot", sender_id="55", user_message="FRGP"
            )
        assert out is not None
        assert "Campaign code lookup" in out["context"]
        assert "no matching active gig" in out["context"]
        assert "doesn’t match any active campaign" in out["context"]

    def test_records_interest_on_campaign_code_hit(self, chatwoot_env):
        hit = (
            '{"_type":"campaign_code_match","query":"ROGUETT","items":'
            '[{"_id":"69b8614f1083b9302fd0a9a7","name":"[Rogue](https://x/explore/69b8614f1083b9302fd0a9a7)",'
            '"effective_payout":10,"end_date":"2026-10-01","stores":[]}],"error":null}'
        )
        interest = '{"_type":"user_gig_interest","created":true,"items":[{}],"error":null}'
        with patch.object(cc, "resolve_member_crwd_id", return_value="aaaaaaaaaaaaaaaaaaaaaaaa"), patch.object(
            cc, "_fetch_member_location", return_value=None
        ), patch(
            "tools.crwd_db.gigs._lookup_campaign_code", return_value=hit
        ), patch(
            "tools.crwd_db.membership._add_user_gig_interest", return_value=interest
        ) as add_interest:
            out = cc.member_context_hook(
                platform="chatwoot", sender_id="55", user_message="RoGUEtt"
            )
        assert out is not None
        assert "one matching active gig" in out["context"]
        assert "69b8614f1083b9302fd0a9a7" in out["context"]
        add_interest.assert_called_once()

    def test_yes_is_not_a_campaign_code(self, chatwoot_env):
        with patch.object(cc, "resolve_member_crwd_id", return_value="abc123"), patch.object(
            cc, "_fetch_member_location", return_value=None
        ), patch(
            "tools.crwd_db.gigs._lookup_campaign_code"
        ) as lookup:
            out = cc.member_context_hook(
                platform="chatwoot", sender_id="55", user_message="yes"
            )
        assert out is not None
        assert "Campaign code lookup" not in out["context"]
        lookup.assert_not_called()
