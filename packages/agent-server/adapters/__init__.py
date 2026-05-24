"""Voqi adapters — every wrapper that sits between the agent brain and the
outside world.

What lives here:
  - languages.py        widget language code → STT language enum + TTS voice id
  - cartesia_tts.py     hardened TTS service (skip-empty-text, reconnect-on-error)
  - turn_start.py       user-turn-start strategy that drops to 1 word while bot speaks
  - rtvi.py             RTVI processor with bot/client origin tagging
  - frames.py           bucket for custom pipeline frames (currently SessionOpenFrame)

bot.py imports from this package; the agent brain (``brain/``) does not call
adapters directly except for a single SessionOpenFrame import inside a kickoff
handler.
"""
