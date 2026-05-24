"""Master system-prompt Jinja conditional collapse / expansion."""

from __future__ import annotations

from brain.config import InAppRuntimeConfig, InAppTool


def _cfg(**overrides) -> InAppRuntimeConfig:
    base = dict(
        session_uuid="s",
        software_uuid="u",
        software_name="Acme",
        additional_instructions=None,
        software_docs=None,
        software_tldr="Acme is software.",
        tools=[],
        ai_profile=None,
    )
    base.update(overrides)
    return InAppRuntimeConfig(**base)


def test_software_docs_present_takes_precedence_over_tldr():
    cfg = _cfg(software_docs="DOC-FULL-CONTENT", software_tldr="TLDR")
    out = cfg.build_system_message(output_language="English")
    assert "DOC-FULL-CONTENT" in out
    assert "TLDR" not in out
    assert "SOFTWARE KNOWLEDGE BASE" in out
    assert "SOFTWARE TL;DR" not in out


def test_tldr_renders_when_no_docs():
    cfg = _cfg(software_docs=None, software_tldr="TLDR-ONLY")
    out = cfg.build_system_message(output_language="English")
    assert "TLDR-ONLY" in out
    assert "SOFTWARE TL;DR" in out


def test_no_software_name_uses_fallback_phrase():
    cfg = _cfg(software_name=None)
    out = cfg.build_system_message(output_language="English")
    assert "this software" in out
    assert "the product" in out


def test_no_tools_renders_no_action_tools_block():
    cfg = _cfg(tools=[])
    out = cfg.build_system_message(output_language="English")
    assert "No action tools are wired" in out


def test_tools_render_with_confirmation_flag():
    cfg = _cfg(
        tools=[
            InAppTool(name="ping", description="ping it", parameters={}),
            InAppTool(
                name="zap",
                description="zap it",
                parameters={},
                requires_confirmation=True,
            ),
        ]
    )
    out = cfg.build_system_message(output_language="English")
    assert "`ping`" in out and "ping it" in out
    assert "`zap`" in out and "zap it" in out
    assert "(requires visitor confirmation before running)" in out


def test_ai_profile_name_and_personality_render():
    cfg = _cfg(ai_profile={"name": "Nora", "personality": "calm and concise"})
    out = cfg.build_system_message(output_language="English")
    assert "Nora" in out
    assert "calm and concise" in out
    assert "AGENT PERSONALITY" in out


def test_no_ai_profile_no_personality_block():
    cfg = _cfg(ai_profile=None)
    out = cfg.build_system_message(output_language="English")
    assert "AGENT PERSONALITY" not in out


def test_software_docs_not_truncated():
    huge = "X" * 50000
    cfg = _cfg(software_docs=huge)
    out = cfg.build_system_message(output_language="English")
    assert huge in out


def test_output_language_is_inserted():
    cfg = _cfg()
    out = cfg.build_system_message(output_language="Spanish")
    assert "Always respond in Spanish" in out


def test_additional_instructions_block_renders_when_provided():
    cfg = _cfg(additional_instructions="Be extra friendly.")
    out = cfg.build_system_message(output_language="English")
    assert "AGENT-SPECIFIC INSTRUCTIONS" in out
    assert "Be extra friendly." in out


def test_static_prompt_does_not_carry_per_round_mechanics():
    """The static system prompt is now slim — per-round mechanics
    (TWO-TRIGGER RULE, PARALLEL batch rule, INTERRUPTING, TOOL
    INVOCATIONS WITH CONFIRMATION, DEMONSTRATION BUDGET, TURN
    COMPLETENESS) all live in the per-round state-context block now.
    The static prompt only carries session-static facts (persona,
    scope, software, tool list, output language)."""
    cfg = _cfg()
    out = cfg.build_system_message(output_language="English")
    for forbidden in (
        "TWO-TRIGGER RULE",
        "INVOCATIONS IN A BATCH ARE PARALLEL",
        "INTERRUPTING AN ACTIVE DEMONSTRATION",
        "TOOL INVOCATIONS WITH CONFIRMATION",
        "DEMONSTRATION BUDGET",
        "TURN COMPLETENESS",
    ):
        assert forbidden not in out, (
            f"static prompt should no longer contain '{forbidden}' — "
            f"that section now lives in the per-round state-context block"
        )


def test_static_prompt_does_not_meta_explain_schema_to_llm():
    """The template no longer carries a meta-section explaining how
    conditional rendering / schema gating works to the LLM. The schema
    does its job silently — the prompt just shows what's relevant
    THIS turn."""
    cfg = _cfg()
    out = cfg.build_system_message(output_language="English")
    # Banned meta-explanations:
    assert "HOW EACH TURN WORKS" not in out
    assert "your schema for the turn enforces that" not in out
    assert "literally cannot emit fields" not in out


def test_static_prompt_carries_ground_rules():
    cfg = _cfg()
    out = cfg.build_system_message(output_language="English")
    assert "GROUND RULES" in out
    assert "Replies are spoken aloud" in out
    assert "Never claim something happened unless a tool result confirms it" in out
    # Tool-error guidance present (the v13 failure fix).
    assert "tool returned an error" in out
