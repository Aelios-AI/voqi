"""Hardened Cartesia TTS service.

Adds two guards on top of the upstream ``CartesiaTTSService``:

  - skip-empty-text: ``"."`` and whitespace-only chunks are dropped instead
    of being shipped to Cartesia (which would reject them).
  - reconnect-on-error: if the WebSocket send fails, the service disconnects
    and reconnects in-line so the next chunk has a fresh socket.

Both guards are in-line on a hot path; the alternative (letting
upstream errors propagate as ``EndFrame``s) drops the entire pipeline
on a single network blip, which is the wrong failure mode for a voice
session that just needs to retry one synthesis call.
"""

from typing import AsyncGenerator

from loguru import logger
from pipecat.frames.frames import ErrorFrame, Frame, TTSStoppedFrame
from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.utils.tracing.service_decorators import traced_tts
from websockets.protocol import State


class AdaptedCartesiaTTSService(CartesiaTTSService):
    @traced_tts
    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame, None]:
        if text.replace(".", "").strip() == "":
            yield None
            return

        if not self._is_streaming_tokens:
            logger.debug(f"{self}: Generating TTS [{text}]")
        else:
            logger.trace(f"{self}: Generating TTS [{text}]")

        try:
            if not self._websocket or self._websocket.state is State.CLOSED:
                await self._connect()

            msg = self._build_msg(text=text, context_id=context_id)

            try:
                await self._get_websocket().send(msg)
                await self.start_tts_usage_metrics(text)
            except Exception as e:
                yield ErrorFrame(error=f"Unknown error occurred: {e}")
                yield TTSStoppedFrame(context_id=context_id)
                await self._disconnect()
                await self._connect()
                return
            yield None
        except Exception as e:
            yield ErrorFrame(error=f"Unknown error occurred: {e}")
