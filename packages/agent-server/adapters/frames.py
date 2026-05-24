"""Custom pipeline frames.

Frames are the communication primitive between processors in the
Pipecat pipeline — every event (audio chunk, transcription, LLM
output, control signal) travels as a frame. Any custom frame the
agent server needs lives here; add new dataclasses below as needed.
"""

from dataclasses import dataclass

from pipecat.frames.frames import ControlFrame, UninterruptibleFrame


@dataclass
class SessionOpenFrame(ControlFrame, UninterruptibleFrame):
    """Queued once the visitor's WebRTC client connects. The agent processor
    uses this signal to flush its kickoff watchdog and confirm the runtime is
    wired end-to-end before accepting user audio."""

    pass
