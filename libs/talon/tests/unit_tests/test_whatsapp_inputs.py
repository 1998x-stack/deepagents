"""WhatsApp preparation ownership is released on completion or cancellation."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from deepagents_talon.channels.base import dispatch_message
from deepagents_talon.channels.whatsapp import WhatsAppChannel, WhatsAppChannelConfig
from deepagents_talon.interfaces import ChannelMessage
from tests.channels.test_whatsapp import RecordingTransport
from tests.unit_tests.test_input_lifecycle import make_host

if TYPE_CHECKING:
    from pathlib import Path


async def test_whatsapp_prepares_only_authorized_envelopes(tmp_path: Path) -> None:
    admitted: dict[str, object] = {
        "chat_id": "self",
        "message_id": "allowed",
        "user_id": "self",
        "text": "allowed",
        "from_self": True,
        "self_chat": True,
        "has_media": True,
        "preparation_token": "allowed-token",
    }
    rejected = {
        **admitted,
        "message_id": "rejected",
        "from_self": False,
        "preparation_token": "rejected-token",
    }

    class Transport(RecordingTransport):
        async def post(self, path: str, payload: dict[str, object]) -> object:
            if path in {"/claim", "/release"}:
                return await super().post(path, payload)
            assert path == "/prepare"
            assert payload == {
                "preparation_token": "allowed-token",
                "chat_id": "self",
                "message_id": "allowed",
            }
            self.posts.append((path, payload))
            return {**admitted, "preparation_token": None}

    transport = Transport([rejected, admitted])
    channel = WhatsAppChannel(
        WhatsAppChannelConfig(session_dir=tmp_path, poll_interval_seconds=60),
        transport=transport,
    )
    received: asyncio.Queue[ChannelMessage] = asyncio.Queue()

    async def receive(message: ChannelMessage) -> None:
        received.put_nowait(message)

    channel.set_message_handler(receive)
    await channel.start()
    try:
        assert (await received.get()).text == "allowed"
        assert [path for path, _ in transport.posts].count("/prepare") == 1
        assert received.empty()
    finally:
        await channel.stop()
    released = [
        entry["preparation_token"]
        for path, payload in transport.posts
        if path == "/release"
        for entry in payload["inputs"]
    ]
    assert set(released) == {"allowed-token", "rejected-token"}


class OwnedPreparation:
    def __init__(self) -> None:
        self.ready = asyncio.Event()
        self.released = asyncio.Event()

    async def __call__(self, message: ChannelMessage) -> ChannelMessage:
        await self.ready.wait()
        return message

    def release(self) -> None:
        self.released.set()


@pytest.mark.parametrize("command", ["/stop", "/new", "/reset-all-history"])
async def test_discard_releases_preparing_and_queued_inputs(tmp_path: Path, command: str) -> None:
    host, channel, agent = make_host(tmp_path)
    preparations = [OwnedPreparation(), OwnedPreparation()]
    await host.start()
    try:
        for index, prepare in enumerate(preparations):
            await dispatch_message(
                channel.handler,
                ChannelMessage("chat", str(index)),
                provider="test",
                prepare=prepare,
            )
        await channel.receive(command)
        assert all(prepare.released.is_set() for prepare in preparations)
        assert agent.requests.empty()
    finally:
        await host.stop()


@pytest.mark.parametrize("reason", ["help", "capacity", "completed", "shutdown"])
async def test_host_releases_owned_preparation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reason: str
) -> None:
    host, channel, agent = make_host(tmp_path)
    prepare = OwnedPreparation()
    if reason == "capacity":
        monkeypatch.setattr("deepagents_talon.host._MAX_CONVERSATION_INPUTS", 0)
    if reason == "completed":
        prepare.ready.set()
    await host.start()
    try:
        await dispatch_message(
            channel.handler,
            ChannelMessage("chat", "/help" if reason == "help" else "hello"),
            provider="test",
            prepare=prepare,
        )
        if reason == "completed":
            assert (await agent.requests.get()).text == "hello"
        if reason == "shutdown":
            await host.stop()
        await prepare.released.wait()
    finally:
        await host.stop()


@pytest.mark.parametrize("stage", ["claim", "dispatch"])
@pytest.mark.parametrize("cancel", [False, True])
async def test_whatsapp_releases_untransferred_batch_on_interruption(
    tmp_path: Path, stage: str, *, cancel: bool
) -> None:
    envelopes = [
        {
            "chat_id": "self",
            "message_id": str(index),
            "user_id": "self",
            "text": "hello",
            "from_self": True,
            "self_chat": True,
            "preparation_token": f"token-{index}",
        }
        for index in range(3)
    ]
    entered = asyncio.Event()

    async def interrupt() -> None:
        entered.set()
        if cancel:
            await asyncio.Event().wait()
        msg = "interrupted batch"
        raise RuntimeError(msg)

    class Transport(RecordingTransport):
        async def post(self, path: str, payload: dict[str, object]) -> object:
            self.posts.append((path, payload))
            if path == "/claim" and stage == "claim":
                await interrupt()
            if path == "/prepare":
                return {**envelopes[int(payload["message_id"])], "preparation_token": None}
            return {"success": True}

    transport = Transport(envelopes)
    channel = WhatsAppChannel(
        WhatsAppChannelConfig(session_dir=tmp_path, poll_interval_seconds=60), transport=transport
    )

    async def receive(_message: ChannelMessage) -> None:
        await interrupt()

    channel.set_message_handler(receive)
    await channel.start()
    try:
        await entered.wait()
        if not cancel:
            with pytest.raises(RuntimeError, match="interrupted batch"):
                await channel._poll
    finally:
        await channel.stop()
    released = {
        entry["preparation_token"]
        for path, payload in transport.posts
        if path == "/release"
        for entry in payload["inputs"]
    }
    assert released == {"token-0", "token-1", "token-2"}
