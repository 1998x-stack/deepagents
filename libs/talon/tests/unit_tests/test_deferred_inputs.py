"""Channels admit inputs before asynchronous attachment preparation."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from deepagents_talon.channels.base import dispatch_message
from deepagents_talon.channels.slack import _SlackInboundMessage
from deepagents_talon.config import TalonConfig
from deepagents_talon.host import TalonHost
from deepagents_talon.interfaces import ChannelMessage
from tests.channels.test_slack import OPERATOR, _channel
from tests.unit_tests.test_input_lifecycle import RecordingAgent, make_host

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("command", ["/stop", "/new", "/reset-all-history"])
async def test_control_invalidates_preparing_input(tmp_path: Path, command: str) -> None:
    host, channel, agent = make_host(tmp_path)
    entered, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def prepare(message: ChannelMessage) -> ChannelMessage:
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            # A download in a worker thread can finish even after its waiter is cancelled.
            await release.wait()
        finished.set()
        return message

    await host.start()
    try:
        await dispatch_message(
            channel.handler,
            ChannelMessage("chat", "older attachment"),
            provider="test",
            prepare=prepare,
        )
        await entered.wait()
        await channel.receive("independent", conversation_id="other")
        assert (await agent.requests.get()).text == "independent"
        await channel.receive(command)
        release.set()
        await finished.wait()
        await channel.receive("later")
        assert (await agent.requests.get()).text == "later"
        assert agent.requests.empty()
    finally:
        release.set()
        await host.stop()


@pytest.mark.parametrize("stalled", [False, True])
async def test_replacement_preserves_input_order(tmp_path: Path, *, stalled: bool) -> None:
    host, channel, agent = make_host(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def prepare(message: ChannelMessage) -> ChannelMessage:
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return message

    await host.start()
    try:
        await dispatch_message(
            channel.handler,
            ChannelMessage("chat", "Only work in staging."),
            provider="test",
            prepare=prepare,
        )
        if stalled:
            await entered.wait()
        await channel.receive("Now run cleanup.")
        release.set()
        request = await agent.requests.get()
        assert request.text == "Only work in staging.\n\nNow run cleanup."
        inputs = request.metadata["talon_inputs"]
        assert isinstance(inputs, list)
        assert [item["content"] for item in inputs] == [
            "Only work in staging.",
            "Now run cleanup.",
        ]
        assert len({item["id"] for item in inputs}) == 2
        assert calls == 1
    finally:
        release.set()
        await host.stop()


async def test_slack_stop_bypasses_stalled_attachment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    channel, gateway, _, _ = _channel(tmp_path)
    agent = RecordingAgent()
    config = TalonConfig.from_env({"AGENT_ASSISTANT_ID": "test"}, base_home=tmp_path)
    host = TalonHost(config=config, agent=agent, channels=[channel])
    entered, release = asyncio.Event(), asyncio.Event()

    async def prepare(message: ChannelMessage, _files: object) -> ChannelMessage:
        entered.set()
        await release.wait()
        return message

    monkeypatch.setattr(channel, "_prepare_inbound_media", prepare)
    await host.start()
    try:
        await gateway.handle_message(
            _SlackInboundMessage(
                channel_id="DCHAT",
                sender_id=OPERATOR,
                text="older attachment",
                ts="1",
                is_dm=True,
                thread_ts=None,
            )
        )
        await entered.wait()
        await gateway.handle_message(
            _SlackInboundMessage(
                channel_id="DCHAT",
                sender_id=OPERATOR,
                text="/stop",
                ts="2",
                is_dm=True,
                thread_ts=None,
            )
        )
        release.set()
        await gateway.handle_message(
            _SlackInboundMessage(
                channel_id="DCHAT",
                sender_id=OPERATOR,
                text="later",
                ts="3",
                is_dm=True,
                thread_ts=None,
            )
        )
        assert (await agent.requests.get()).text == "later"
        assert agent.requests.empty()
    finally:
        release.set()
        await host.stop()
