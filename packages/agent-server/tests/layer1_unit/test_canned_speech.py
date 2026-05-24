"""Multilingual canned-speech rendering: every key×lang resolves; unknown
languages fall back to English."""

from __future__ import annotations

import pytest

from brain.canned_speech import _TRANSLATIONS, CannedKey, render_canned

EXPECTED_LANGS = {
    "en", "es", "fr", "de", "it", "pt", "nl", "ru", "ja", "ko", "hi", "vi",
    "id", "tr", "pl", "sv", "da", "fi", "cs", "ar", "ms", "zh", "th",
}


@pytest.mark.parametrize("key", list(CannedKey))
def test_every_key_has_full_translation_set(key):
    bundle = _TRANSLATIONS[key]
    missing = EXPECTED_LANGS - set(bundle.keys())
    assert not missing, f"{key} missing langs: {missing}"
    extras = set(bundle.keys()) - EXPECTED_LANGS
    assert not extras, f"{key} unexpected langs: {extras}"


@pytest.mark.parametrize("key", list(CannedKey))
@pytest.mark.parametrize("lang", sorted(EXPECTED_LANGS))
def test_every_translation_non_empty(key, lang):
    text = render_canned(key, lang)
    assert text and len(text.strip()) >= 3


@pytest.mark.parametrize("key", list(CannedKey))
def test_unknown_language_falls_back_to_english(key):
    en = render_canned(key, "en")
    fallback = render_canned(key, "zz")  # not a real ISO code
    assert fallback == en


def test_response_timeout_does_not_use_slash_in_english():
    """Wording fix from prior session: 'the latest response' (no slash)."""
    en = render_canned(CannedKey.RESPONSE_TIMEOUT, "en")
    assert "/" not in en


def test_batch_ceiling_text_explicitly_ends_demo():
    """User asked for the wording to clearly tell the visitor the
    demonstration is being ended because of the budget cap."""
    en = render_canned(CannedKey.BATCH_CEILING_HIT, "en")
    lower = en.lower()
    assert "limit" in lower or "limit" in en
    assert any(p in lower for p in ("end", "stop"))
