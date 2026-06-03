"""Custom RTVI processor with client/bot origin tagging."""

import pipecat.processors.frameworks.rtvi.models as RTVI
from loguru import logger
from pipecat.frames.frames import (
    InputTransportMessageFrame,
    OutputTransportMessageUrgentFrame,
)
from pipecat.processors.frameworks.rtvi import RTVIProcessor
from pydantic import BaseModel, ValidationError


class AdaptedRTVIProcessor(RTVIProcessor):
    """RTVIProcessor with explicit client/bot origin tagging.

    Every outgoing message gets data.from = "bot" stamped on it via
    push_transport_message. On the way in, _handle_transport_message only
    processes messages where data.from == "client". Everything else (our own
    loopbacks) is silently dropped.

    The frontend must include { from: "client" } in the data of every message
    it sends to the bot.
    """

    async def push_transport_message(self, model: BaseModel, exclude_none: bool = True):
        """Stamp data.from = 'bot' on every outgoing message before sending."""
        message = model.model_dump(exclude_none=exclude_none)
        if not isinstance(message.get("data"), dict):
            message["data"] = {}
        message["data"]["from"] = "bot"
        await self.push_frame(OutputTransportMessageUrgentFrame(message=message))

    async def _handle_transport_message(self, frame: InputTransportMessageFrame):
        """Handle an incoming transport message frame."""
        try:
            transport_message = frame.message
            data = transport_message.get("data") or {}
            # The Pipecat SDK auto-sends RTVI system messages
            # (client-ready, disconnect-bot) WITHOUT any `from` field —
            # the payload is just `{version, about}`. A strict
            # `from != "client" → drop` filter silently swallows
            # client-ready, the bot never responds with bot-ready, and
            # the SDK stays at "connected" state forever (so
            # `RTVIEvent.BotReady` never fires on the client). The
            # ONLY thing this filter is meant to reject is our own
            # outbound messages looped back, which `push_transport_message`
            # stamps with `from = "bot"`. Everything else passes.
            from_field = data.get("from") or (data.get("d") or {}).get("from")
            if from_field == "bot":
                return
            if transport_message.get("label") != RTVI.MESSAGE_LABEL:
                logger.warning(f"Ignoring not RTVI message: {transport_message}")
                return
            message = RTVI.Message.model_validate(transport_message)
            # Pipecat's RTVIProcessor exposes its inbound-message queue
            # as ``_message_queue`` — that's what its own pump reads
            # from. We're delegating into the parent's pipeline here,
            # so the parent's attribute name is what matters; don't
            # rename to anything AeliosSpark-specific.
            await self._message_queue.put(message)
        except ValidationError as e:
            await self.send_error(f"Invalid RTVI transport message: {e}")
            logger.warning(f"Invalid RTVI transport message: {e}")
