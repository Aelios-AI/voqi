"""User-turn-start strategy with bot-aware interruption sensitivity.

The base ``MinWordsUserTurnStartStrategy`` requires N words before treating
incoming audio as a user turn. That's the right floor when nobody is
talking — single-word noise shouldn't flip the agent into "user is
speaking" mode. But while the bot itself is mid-utterance, the same
threshold suppresses real interruptions ("stop", "wait") below N words.

This subclass drops the threshold to 1 word whenever the bot is currently
speaking, so short interruptions register, then restores the configured
floor once the bot stops.
"""

from pipecat.frames.frames import InterimTranscriptionFrame, TranscriptionFrame
from pipecat.turns.types import ProcessFrameResult
from pipecat.turns.user_start import MinWordsUserTurnStartStrategy


class AdaptedMinWordsUserTurnStartStrategy(MinWordsUserTurnStartStrategy):
    async def _handle_transcription(
        self, frame: TranscriptionFrame | InterimTranscriptionFrame
    ) -> ProcessFrameResult:
        min_words = self._min_words if self._bot_speaking else 1
        word_count = len(frame.text.split())
        if word_count >= min_words:
            await self.trigger_user_turn_started()
            return ProcessFrameResult.STOP
        await self.trigger_reset_aggregation()
        return ProcessFrameResult.CONTINUE
