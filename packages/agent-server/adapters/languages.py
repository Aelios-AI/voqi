"""Language helpers — STT codes, TTS voice ids, human-readable names.

The widget sends a language code on /start; the agent server uses it
twice: ``get_deepgram_stt_language`` picks the Deepgram Nova-3 enum so
STT recognises the right language, and ``resolve_cartesia_voice_id``
picks the Cartesia TTS voice so the agent's reply sounds native.

Voqi supports 37 languages — the intersection of Deepgram Nova-3 (STT)
and Cartesia (TTS) coverage that we ship native Cartesia voices for.
All bundled voices are FEMALE, so if you set ``agent.name`` in
``voqi.config.yaml`` pick a feminine name; otherwise the spoken voice
and the persona name won't match. Operators who want a different
gender / voice per language should supply Cartesia voice ids via the
agent profile's ``voice_languages`` override, or replace entries in
``CARTESIA_TTS_VOICES`` below.
"""

from loguru import logger
from pipecat.transcriptions.language import Language

LANGUAGE_NAMES: dict[Language, str] = {
    Language.AR: "Arabic",
    Language.BG: "Bulgarian",
    Language.ZH: "Chinese",
    Language.ZH_CN: "Chinese",
    Language.HR: "Croatian",
    Language.CS: "Czech",
    Language.DA: "Danish",
    Language.NL: "Dutch",
    Language.EN: "English",
    Language.FI: "Finnish",
    Language.FR: "French",
    Language.DE: "German",
    Language.EL: "Greek",
    Language.GU: "Gujarati",
    Language.HE: "Hebrew",
    Language.HI: "Hindi",
    Language.HU: "Hungarian",
    Language.ID: "Indonesian",
    Language.IT: "Italian",
    Language.JA: "Japanese",
    Language.KN: "Kannada",
    Language.KO: "Korean",
    Language.MS: "Malay",
    Language.MR: "Marathi",
    Language.NO: "Norwegian",
    Language.PL: "Polish",
    Language.PT: "Portuguese",
    Language.RO: "Romanian",
    Language.RU: "Russian",
    Language.SK: "Slovak",
    Language.ES: "Spanish",
    Language.SV: "Swedish",
    Language.TL: "Tagalog",
    Language.TA: "Tamil",
    Language.TE: "Telugu",
    Language.TH: "Thai",
    Language.TR: "Turkish",
    Language.VI: "Vietnamese",
}


def get_language_name(language: Language) -> str:
    """Human-readable language name; falls back to English."""
    return LANGUAGE_NAMES.get(language, "English")


# Widget language code → Pipecat ``Language`` enum. Deepgram Nova-3
# accepts every entry below directly via per-session
# ``language=<Language enum>``. Pipecat's ``Language`` enum extends
# ``str`` and maps each BCP-47 code to a member, so most entries are
# identity-style. Note: the widget uses ``ms`` for Malay (standard
# ISO), distinct from ``ml`` which is Malayalam.
DEEPGRAM_STT_LANGUAGES: dict[str, Language] = {
    "ar": Language.AR,
    "bg": Language.BG,
    "zh": Language.ZH,
    "hr": Language.HR,
    "cs": Language.CS,
    "da": Language.DA,
    "nl": Language.NL,
    "en": Language.EN,
    "fi": Language.FI,
    "fr": Language.FR,
    "de": Language.DE,
    "el": Language.EL,
    "gu": Language.GU,
    "he": Language.HE,
    "hi": Language.HI,
    "hu": Language.HU,
    "id": Language.ID,
    "it": Language.IT,
    "ja": Language.JA,
    "kn": Language.KN,
    "ko": Language.KO,
    "ms": Language.MS,
    "mr": Language.MR,
    "no": Language.NO,
    "pl": Language.PL,
    "pt": Language.PT,
    "ro": Language.RO,
    "ru": Language.RU,
    "sk": Language.SK,
    "es": Language.ES,
    "sv": Language.SV,
    "tl": Language.TL,
    "ta": Language.TA,
    "te": Language.TE,
    "th": Language.TH,
    "tr": Language.TR,
    "vi": Language.VI,
}


def get_deepgram_stt_language(language_code: str) -> Language:
    """Convert a widget language code to the ``Language`` enum value
    Pipecat's DeepgramSTTService accepts. Falls back to English for
    unknown codes."""
    return DEEPGRAM_STT_LANGUAGES.get(language_code, Language.EN)


# ──────────────────────────────────────────────────────────────────────
# Cartesia TTS voice catalogue
# ──────────────────────────────────────────────────────────────────────
# Default voice id per language. All bundled voices are FEMALE — pair
# ``agent.name`` in voqi.config.yaml with a feminine name, or override
# the voice via the agent profile's ``voice_languages`` to use a
# different one. When ``voice_languages`` carries an entry for the
# visitor's language, that wins; otherwise we fall back to this map.
# Languages without an entry here (or with an empty-string placeholder)
# fall back to the English voice with a warning log.

CARTESIA_TTS_VOICES: dict[Language, str] = {
    Language.EN: "6ccbfb76-1fc6-48f7-b71d-91ac6298247b",
    Language.ES: "ccfea4bf-b3f4-421e-87ed-dd05dae01431",
    Language.FR: "65b25c5d-ff07-4687-a04c-da2f43ef6fa9",
    Language.DE: "38aabb6a-f52b-4fb0-a3d1-988518f4dc06",
    Language.IT: "d718e944-b313-4998-b011-d1cc078d4ef3",
    Language.PT: "d4b44b9a-82bc-4b65-b456-763fce4c52f9",
    Language.NL: "0eb213fe-4658-45bc-9442-33a48b24b133",
    Language.RU: "064b17af-d36b-4bfb-b003-be07dba1b649",
    Language.JA: "2b568345-1d48-4047-b25f-7baccf842eb0",
    Language.KO: "304fdbd8-65e6-40d6-ab78-f9d18b9efdf9",
    Language.HI: "56e35e2d-6eb6-4226-ab8b-9776515a7094",
    Language.VI: "b8cd71e3-bc14-4538-a530-d6314731c036",
    Language.ID: "b441c4fd-4910-4c55-ae56-f0291057e2cc",
    Language.TR: "0f95596c-09c4-4418-99fe-5c107e0713c0",
    Language.PL: "dcf62f33-7cff-4f20-85b2-2efaa68cbc32",
    Language.SV: "f852eb8d-a177-48cd-bf63-7e4dcab61a36",
    Language.DA: "c323c793-41f9-47b8-99dc-9b44b0440b84",
    Language.FI: "65c34eec-42c9-4a75-a8bd-b676fb847b72",
    Language.CS: "bdc4a3ce-2e22-4398-8cd6-76b7160d2298",
    Language.AR: "002622d8-19d0-4567-a16a-f99c7397c062",
    Language.ZH: "f9a4b3a6-b44b-469f-90e3-c8e19bd30e99",
    Language.ZH_CN: "f9a4b3a6-b44b-469f-90e3-c8e19bd30e99",
    Language.TH: "ccc7bb22-dcd0-42e4-822e-0731b950972f",
    Language.MS: "83604597-55fa-4ccc-8357-730b313f353f",
    Language.BG: "fcbecbcc-0cef-4615-8b5a-712fe1b39dd0",
    Language.HR: "2a2624ad-bd06-4563-81fd-0519742e25d2",
    Language.EL: "50849023-76e9-46c7-af52-9ec39888a165",
    Language.GU: "4590a461-bc68-4a50-8d14-ac04f5923d22",
    Language.HE: "ff857c8e-e7f9-4afd-af42-dce9f3c5ab02",
    Language.HU: "e97c3b37-1aa5-46af-afb7-9545086aaa92",
    Language.KN: "7c6219d2-e8d2-462c-89d8-7ecba7c75d65",
    Language.MR: "5c32dce6-936a-4892-b131-bafe474afe5f",
    Language.NO: "4f7b1820-6263-4615-87a7-b105768d8f64",
    Language.RO: "34acfaee-c556-41ee-a5f6-c687fb20357c",
    Language.SK: "abf68668-6549-462c-8426-1fa7b466b91d",
    Language.TL: "6d14ac2a-4dda-46f8-bd6f-0722db08ec00",
    Language.TA: "80e4e2b3-ec54-4930-97ac-667eba950352",
    Language.TE: "4418bb06-8329-49a1-bb11-53bb64ca0547",
}


def get_cartesia_tts_config(language: Language) -> str:
    """Default Cartesia voice id for a Pipecat ``Language`` value.

    Used as the fallback when the agent profile's ``voice_languages``
    list doesn't carry a per-language override. Languages with no
    entry — or an empty-string placeholder — fall back to the English
    voice with a warning log.
    """
    voice = CARTESIA_TTS_VOICES.get(language)
    if not voice:
        logger.warning(
            f"[voqi] No native Cartesia voice id for language={language}; "
            f"falling back to the English voice. Add a voice id to "
            f"adapters/languages.py or override via voice_languages."
        )
        return CARTESIA_TTS_VOICES[Language.EN]
    return voice


def resolve_cartesia_voice_id(
    *,
    chosen_language_code: str,
    chosen_language: Language,
    voice_languages: list[dict] | None,
) -> str:
    """Pick the Cartesia voice id for this session.

    Resolution order:

      1. ``voice_languages`` entry whose ``language`` matches the
         visitor's chosen language (per-agent override).
      2. ``voice_languages`` entry for English (sensible fallback).
      3. ``CARTESIA_TTS_VOICES`` default for the chosen language.
    """
    if voice_languages:
        for v in voice_languages:
            if v.get("language") == chosen_language_code and v.get("cartesiaVoiceId"):
                return v["cartesiaVoiceId"]
        for v in voice_languages:
            if v.get("language") == "en" and v.get("cartesiaVoiceId"):
                return v["cartesiaVoiceId"]
    return get_cartesia_tts_config(chosen_language)
