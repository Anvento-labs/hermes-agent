"""Bedrock client rebuild correctness in recovery paths.

Regression tests for the Aug 2026 prod incident: after a transport failure
on the bedrock provider, ``try_recover_primary_transport`` (and
``restore_primary_runtime``) rebuilt the client with the plain
``build_anthropic_client`` — an unsigned Anthropic-API client pointed at the
Bedrock endpoint. Bedrock answers those requests with
``UnknownOperationException`` and zero SSE events, which the SDK streaming
path surfaces as a bare empty ``AssertionError`` that no retry policy
recognizes, leaving the session stuck on a broken client.

Also covers the deferred-rebuild handshake: watchdog threads (interrupt /
stale-call kill) must only socket-abort the shared Anthropic client and set
``_anthropic_client_needs_rebuild``; the owner thread rebuilds on the next
request in ``_anthropic_messages_create``.
"""

from unittest.mock import MagicMock, patch

from run_agent import AIAgent


def _make_tool_defs(*names: str) -> list:
    return [
        {
            "type": "function",
            "function": {
                "name": n,
                "description": f"{n} tool",
                "parameters": {"type": "object", "properties": {}},
            },
        }
        for n in names
    ]


def _make_bedrock_agent(bedrock_client=None):
    """Create a minimal AIAgent on the bedrock provider (anthropic_messages)."""
    bedrock_client = bedrock_client if bedrock_client is not None else MagicMock()
    with (
        patch("run_agent.get_tool_definitions", return_value=_make_tool_defs("web_search")),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
        patch(
            "agent.anthropic_adapter.build_anthropic_bedrock_client",
            return_value=bedrock_client,
        ),
    ):
        agent = AIAgent(
            api_key="aws-sdk",
            base_url="https://bedrock-runtime.us-east-2.amazonaws.com",
            provider="bedrock",
            api_mode="anthropic_messages",
            model="us.anthropic.claude-sonnet-4-6",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
        )
    return agent


def _make_transport_error(error_type="APIConnectionError"):
    cls = type(error_type, (Exception,), {})
    return cls("Connection error.")


class TestBedrockTransportRecovery:
    def test_recovery_rebuilds_with_bedrock_client(self):
        """try_recover_primary_transport must produce a SigV4 Bedrock client."""
        agent = _make_bedrock_agent()
        assert agent._bedrock_region == "us-east-2"
        rebuilt = MagicMock(name="rebuilt_bedrock_client")
        error = _make_transport_error("APIConnectionError")

        with (
            patch(
                "agent.anthropic_adapter.build_anthropic_bedrock_client",
                return_value=rebuilt,
            ) as mock_bedrock_build,
            patch("agent.anthropic_adapter.build_anthropic_client") as mock_plain_build,
            patch("time.sleep"),
        ):
            result = agent._try_recover_primary_transport(
                error, retry_count=3, max_retries=3,
            )

        assert result is True
        mock_bedrock_build.assert_called_once_with("us-east-2")
        mock_plain_build.assert_not_called()
        assert agent._anthropic_client is rebuilt
        assert agent.client is None

    def test_restore_primary_runtime_rebuilds_with_bedrock_client(self):
        agent = _make_bedrock_agent()
        agent._fallback_activated = True
        rebuilt = MagicMock(name="rebuilt_bedrock_client")

        with (
            patch(
                "agent.anthropic_adapter.build_anthropic_bedrock_client",
                return_value=rebuilt,
            ) as mock_bedrock_build,
            patch("agent.anthropic_adapter.build_anthropic_client") as mock_plain_build,
        ):
            result = agent._restore_primary_runtime()

        assert result is True
        mock_bedrock_build.assert_called_once_with("us-east-2")
        mock_plain_build.assert_not_called()
        assert agent._anthropic_client is rebuilt


class TestDeferredAnthropicClientRebuild:
    def test_rebuild_flag_triggers_owner_thread_rebuild(self):
        """_anthropic_messages_create rebuilds the client when flagged."""
        old_client = MagicMock(name="aborted_client")
        agent = _make_bedrock_agent(bedrock_client=old_client)
        agent._anthropic_client_needs_rebuild = True
        rebuilt = MagicMock(name="rebuilt_bedrock_client")

        with (
            patch(
                "agent.anthropic_adapter.build_anthropic_bedrock_client",
                return_value=rebuilt,
            ),
            patch("agent.anthropic_adapter.create_anthropic_message") as mock_create,
        ):
            agent._anthropic_messages_create(
                {"model": agent.model, "max_tokens": 10, "messages": []}
            )

        old_client.close.assert_called_once()
        assert agent._anthropic_client is rebuilt
        assert agent._anthropic_client_needs_rebuild is False
        assert mock_create.call_args[0][0] is rebuilt

    def test_no_rebuild_without_flag(self):
        client = MagicMock(name="healthy_client")
        agent = _make_bedrock_agent(bedrock_client=client)

        with (
            patch(
                "agent.anthropic_adapter.build_anthropic_bedrock_client"
            ) as mock_bedrock_build,
            patch("agent.anthropic_adapter.create_anthropic_message") as mock_create,
        ):
            agent._anthropic_messages_create(
                {"model": agent.model, "max_tokens": 10, "messages": []}
            )

        mock_bedrock_build.assert_not_called()
        client.close.assert_not_called()
        assert mock_create.call_args[0][0] is client
