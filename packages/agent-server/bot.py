"""Voice agent pipeline assembly.

Run with::

    uv run server.py

The pipeline:

  visitor speech (Daily WebRTC)
       ↓
   STT (Deepgram Nova-3, per-session language)
       ↓
   user-turn aggregator (VAD + optional SmartTurn audio analyzer)
       ↓
   InAppAgentProcessor — streams the tool-using LLM loop, dispatches
       tool calls to the visitor's widget over RTVI, waits for results,
       speaks the reply back
       ↓
   TTS (Cartesia, per-language voice from adapters/languages.py)
       ↓
   transport.output() — published to the Daily room

The agent's spoken reply travels two ways: synthesised audio over the
WebRTC track, and transcribed text over the RTVI data channel
(``BotTranscriptionMessage`` events at sentence boundaries). The widget
shows both — audio plays through the speaker, text renders as bubbles.
"""

import asyncio
import os
from pathlib import Path
from typing import Optional

import aiohttp
import yaml
from dotenv import load_dotenv
from loguru import logger

print("🚀 Starting in-app realtime bot...")
print("⏳ Loading models and imports (first run only)\n")

logger.info("Loading Silero VAD model...")
from pipecat.audio.vad.silero import SileroVADAnalyzer  # noqa: E402

logger.info("✅ Silero VAD model loaded")

from pipecat.audio.turn.smart_turn.base_smart_turn import SmartTurnParams
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.pipeline.pipeline import Pipeline  # noqa: E402
from pipecat.pipeline.runner import PipelineRunner  # noqa: E402
from pipecat.pipeline.task import PipelineParams, PipelineTask  # noqa: E402
from pipecat.processors.aggregators.llm_context import LLMContext  # noqa: E402
from pipecat.processors.aggregators.llm_response_universal import (  # noqa: E402
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.runner.types import DailyRunnerArguments, RunnerArguments  # noqa: E402
from pipecat.services.cartesia.tts import (  # noqa: E402
    CartesiaTTSService,
    GenerationConfig,
)
from pipecat.services.deepgram.stt import DeepgramSTTService  # noqa: E402
from pipecat.transcriptions.language import Language  # noqa: E402
from pipecat.transports.daily.transport import (  # noqa: E402
    DailyParams,
    DailyTransport,
)
from pipecat.transports.daily.utils import DailyRESTHelper  # noqa: E402
from pipecat.turns.user_stop.turn_analyzer_user_turn_stop_strategy import (
    TurnAnalyzerUserTurnStopStrategy,
)
from pipecat.turns.user_turn_strategies import UserTurnStrategies  # noqa: E402

from adapters.cartesia_tts import AdaptedCartesiaTTSService  # noqa: E402
from adapters.frames import SessionOpenFrame  # noqa: E402
from adapters.languages import (  # noqa: E402
    get_deepgram_stt_language,
    get_language_name,
    resolve_cartesia_voice_id,
)
from adapters.rtvi import AdaptedRTVIProcessor  # noqa: E402
from adapters.turn_start import AdaptedMinWordsUserTurnStartStrategy  # noqa: E402
from brain import (  # noqa: E402
    InAppAgentProcessor,
    InAppRuntimeConfig,
)

load_dotenv(override=True)

logger.info("✅ All components loaded successfully!")


def _resolve_config_path() -> Path:
    """Resolve the AeliosSpark config file. Order: AELIOS_SPARK_CONFIG env var → CWD
    ``aelios-spark.config.yaml`` → repo-root ``aelios-spark.config.yaml``."""
    explicit = os.getenv("AELIOS_SPARK_CONFIG")
    if explicit:
        return Path(explicit).expanduser()
    cwd = Path.cwd() / "aelios-spark.config.yaml"
    if cwd.exists():
        return cwd
    return Path(__file__).parent / "aelios-spark.config.yaml"


def load_runtime_config(body: dict) -> dict:
    """Build the runtime-config dict from a local YAML file plus the
    widget-supplied session body. All agent configuration (persona,
    knowledge base, additional instructions) lives in
    ``aelios-spark.config.yaml`` next to this file; per-session choices (tools,
    language, mode) come from the widget's ``/start`` POST.

    YAML schema::

        agent:
          name: "TaskTracker Assistant"
          personality: "Friendly and efficient"
        software:
          name: "TaskTracker"
          tldr: "Lightweight kanban for small teams."
          docs_file: "./knowledge.md"   # OR docs: |  <inline markdown>
        additional_instructions: |
          Help the user manage tasks. Be concise.
    """
    cfg_path = _resolve_config_path()
    if not cfg_path.exists():
        raise FileNotFoundError(
            f"AeliosSpark config not found at {cfg_path}. Set AELIOS_SPARK_CONFIG or "
            "place aelios-spark.config.yaml next to bot.py."
        )

    with cfg_path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    agent = cfg.get("agent") or {}
    software = cfg.get("software") or {}

    docs = software.get("docs")
    if not docs:
        docs_file = software.get("docs_file")
        if docs_file:
            docs_path = Path(docs_file).expanduser()
            if not docs_path.is_absolute():
                docs_path = cfg_path.parent / docs_path
            if docs_path.exists():
                docs = docs_path.read_text(encoding="utf-8")
            else:
                logger.warning(f"[aelios-spark] software.docs_file not found: {docs_path}")

    return {
        "session_uuid": body.get("session_uuid", ""),
        "software": {
            "uuid": software.get("uuid"),
            "name": software.get("name"),
        },
        "additional_instructions": cfg.get("additional_instructions"),
        "software_docs": docs,
        "software_tldr": software.get("tldr"),
        "tools": body.get("tools") or [],
        "ai_profile": {
            "name": agent.get("name"),
            "personality": agent.get("personality"),
            # voice_languages omitted — defaults from adapters/languages.py apply
        },
        # Both language and mode are visitor-chosen — the widget MUST
        # send them in the /start body. There is no server-side default;
        # from_response raises if either is missing.
        "language": body.get("language"),
        "mode": body.get("mode"),
        "speech_keywords": cfg.get("speech_keywords") or [],
        "turn_detection": bool(cfg.get("turn_detection")),
        # Optional per-deployment override for the runaway-batch
        # guardrail. None / missing → processor falls back to its
        # built-in default of 8.
        "max_tool_batches_per_demonstration": cfg.get(
            "max_tool_batches_per_demonstration"
        ),
        # Optional per-deployment override for the per-batch tool
        # timeout. None / missing → processor falls back to its
        # built-in default of 60.0 seconds.
        "batch_timeout_seconds": cfg.get("batch_timeout_seconds"),
        # Optional per-deployment override for the OpenAI model id.
        # None / missing → processor falls back to its built-in
        # default (``OPENAI_MODEL`` in processor.py).
        "llm_model": cfg.get("llm_model"),
    }


async def run_in_app_bot(runner_args: DailyRunnerArguments):
    """Per-session bot loop. Boots the Pipecat pipeline for one visitor and
    runs until they disconnect or the session cap fires."""

    KICKOFF_TIMEOUT_SECONDS = int(os.getenv("KICKOFF_TIMEOUT_SECONDS", str(5 * 60)))
    kickoff_received_event = asyncio.Event()
    bot_task = asyncio.current_task()

    async def kickoff_watchdog():
        try:
            await asyncio.wait_for(
                kickoff_received_event.wait(), timeout=KICKOFF_TIMEOUT_SECONDS
            )
            logger.info("[in-app] kickoff received — watchdog exiting")
        except asyncio.TimeoutError:
            logger.warning(
                f"[in-app] kickoff not received within "
                f"{KICKOFF_TIMEOUT_SECONDS}s — killing bot"
            )
            if bot_task is not None and not bot_task.done():
                bot_task.cancel()

    kickoff_watchdog_task = asyncio.create_task(kickoff_watchdog())

    async with aiohttp.ClientSession() as session:
        daily_rest_helper = DailyRESTHelper(
            daily_api_key=os.getenv("DAILY_API_KEY"),
            daily_api_url=os.getenv("DAILY_API_URL", "https://api.daily.co/v1"),
            aiohttp_session=session,
        )
        logger.info("[in-app] starting bot")

        body = getattr(runner_args, "body", {}) or {}

        # Predeclared so the outer ``finally`` can safely reference it
        # even if construction fails before the assignment below.
        agent_processor: Optional[InAppAgentProcessor] = None

        try:
            config_data = load_runtime_config(body)
            runtime_config = InAppRuntimeConfig.from_response(config_data)
            chosen_language_code = runtime_config.language or "en"
            logger.info(
                f"[in-app] loaded runtime config: "
                f"software={runtime_config.software_name} "
                f"tools={len(runtime_config.tools)} "
                f"language={chosen_language_code} "
                f"mode={runtime_config.mode}"
            )

            messages: list[dict] = []
            context = LLMContext(messages)

            # Turn detection is opt-in via ``turn_detection: true`` in
            # aelios-spark.config.yaml. When enabled, Pipecat's local SmartTurn
            # audio analyzer decides when the visitor's turn ends, so
            # VAD can be eager (shorter silence window) — SmartTurn
            # second-guesses any premature cut. When disabled, VAD is
            # the sole arbiter, so we give it a longer silence window
            # to avoid cutting the visitor off mid-thought.
            #
            # SmartTurn only ships models for 21 of aelios-spark's 37 widget
            # languages. For the other 16 — Bulgarian, Croatian, Czech,
            # Greek, Gujarati, Hebrew, Hungarian, Kannada, Malay,
            # Romanian, Slovak, Swedish, Tagalog, Tamil, Telugu, Thai —
            # ignore the config flag and fall back to VAD-only so we
            # don't load an inapplicable analyzer.
            SMART_TURN_UNSUPPORTED = {
                "bg", "hr", "cs", "el", "gu", "he", "hu", "kn",
                "ms", "ro", "sk", "sv", "tl", "ta", "te", "th",
            }
            turn_detection_enabled = bool(config_data.get("turn_detection"))
            if turn_detection_enabled and chosen_language_code in SMART_TURN_UNSUPPORTED:
                logger.info(
                    f"[aelios-spark] turn_detection=true ignored for "
                    f"language={chosen_language_code} — SmartTurn doesn't "
                    f"support it; using VAD-only"
                )
                turn_detection_enabled = False
            if turn_detection_enabled:
                logger.info(f"[aelios-spark] turn detection enabled — using SmartTurn analyzer")
                turn_stop_strategy = [
                    TurnAnalyzerUserTurnStopStrategy(
                        turn_analyzer=LocalSmartTurnAnalyzerV3(
                            params=SmartTurnParams(stop_secs=4.0),
                        ),
                    ),
                ]
            else:
                logger.info(f"[aelios-spark] turn detection disabled — using VAD-only strategy")
                turn_stop_strategy = None

            user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
                context,
                user_params=LLMUserAggregatorParams(
                    user_turn_strategies=UserTurnStrategies(
                        start=[AdaptedMinWordsUserTurnStartStrategy(min_words=2)],
                        stop=turn_stop_strategy,
                    ),
                    vad_analyzer=SileroVADAnalyzer(),
                ),
            )

            rtvi = AdaptedRTVIProcessor()
            chosen_language = Language(chosen_language_code)

            # The agent speaks back via Cartesia TTS — every speech the
            # LLM produces gets synthesised to audio and pushed down
            # the Daily output. The widget can mute the agent locally
            # (visitor preference); the muting happens client-side
            # by silencing the inbound audio track, NOT by suppressing
            # TTS server-side, so the bot's behaviour is uniform.
            params = DailyParams(
                audio_in_enabled=True,
                audio_out_enabled=True,
                video_out_enabled=False,
            )
            transport_layer = DailyTransport(
                runner_args.room_url,
                runner_args.token,
                "bot",
                params=params,
            )

            speech_keywords = config_data.get("speech_keywords") or []
            # Single STT provider for all 37 widget languages — Deepgram
            # Nova-3 accepts each one directly via the ``language``
            # enum on Settings (see DEEPGRAM_STT_LANGUAGES in
            # adapters/languages.py). ``keyterm`` biases recognition
            # toward product names / jargon from aelios-spark.config.yaml.
            speech_to_text = DeepgramSTTService(
                api_key=os.getenv("DEEPGRAM_API_KEY"),
                settings=DeepgramSTTService.Settings(
                    language=get_deepgram_stt_language(chosen_language_code),
                    keyterm=speech_keywords,
                ),
            )

            agent_processor = InAppAgentProcessor(
                rtvi=rtvi,
                user_aggregator=user_aggregator,
                assistant_aggregator=assistant_aggregator,
                runtime_config=runtime_config,
                output_language=get_language_name(chosen_language),
                language_code=chosen_language_code,
                kickoff_received_event=kickoff_received_event,
            )

            # ── TTS ───────────────────────────────────────────────────
            # Cartesia synth for the agent's spoken replies. Voice id
            # resolves from the agent profile's ``voice_languages``
            # list (per-agent override) → English profile entry →
            # CARTESIA_TTS_VOICES default for the chosen language.
            voice_languages = (
                runtime_config.ai_profile.get("voice_languages")
                if runtime_config.ai_profile
                else None
            )
            voice_id = resolve_cartesia_voice_id(
                chosen_language_code=chosen_language_code,
                chosen_language=chosen_language,
                voice_languages=voice_languages,
            )
            logger.info(
                f"[in-app] tts voice resolved: language={chosen_language_code} "
                f"voice_id={voice_id[:8]}…"
            )
            # English uses the "confident" generation config for a warmer
            # read; other languages aren't tuned per-locale yet.
            if chosen_language == Language.EN:
                tts_settings = CartesiaTTSService.Settings(
                    voice=voice_id,
                    language=chosen_language,
                    generation_config=GenerationConfig(emotion="confident"),
                )
            else:
                tts_settings = CartesiaTTSService.Settings(
                    voice=voice_id,
                    language=chosen_language,
                )
            text_to_speech = AdaptedCartesiaTTSService(
                model="sonic-3.5",
                settings=tts_settings,
                api_key=os.getenv("CARTESIA_API_KEY"),
            )

            # Transport layer i/o
            transport_layer_input = transport_layer.input()
            transport_layer_output = transport_layer.output()
            pipeline = Pipeline(
                [
                    transport_layer_input,
                    speech_to_text,
                    user_aggregator,
                    agent_processor,
                    # TTS sits between the agent's text output and the
                    # Daily transport. Every LLMTextFrame the processor
                    # streams goes through here, gets synthesised to
                    # TTSAudioRawFrames, then is published to the
                    # Daily room.
                    text_to_speech,
                    transport_layer_output,
                    assistant_aggregator,
                ]
            )

            task = PipelineTask(
                pipeline,
                params=PipelineParams(
                    enable_metrics=True,
                    enable_usage_metrics=True,
                ),
                rtvi_processor=rtvi,
            )

            _kickoff_sent = False

            @transport_layer.event_handler("on_client_connected")
            async def on_client_connected(transport, client):
                nonlocal _kickoff_sent
                logger.info("[in-app] client connected")
                if not _kickoff_sent:
                    _kickoff_sent = True
                    kickoff_received_event.set()
                    await task.queue_frames([SessionOpenFrame()])

            @transport_layer.event_handler("on_client_disconnected")
            async def on_client_disconnected(transport, client):
                logger.info("[in-app] client disconnected")
                await task.cancel()

            runner = PipelineRunner(handle_sigint=runner_args.handle_sigint)
            await runner.run(task)

        finally:
            try:
                await daily_rest_helper.delete_room_by_url(runner_args.room_url)
            except Exception as e:
                logger.error(f"[aelios-spark] failed to delete Daily room: {e}")

            if not kickoff_watchdog_task.done():
                kickoff_watchdog_task.cancel()
                try:
                    await kickoff_watchdog_task
                except (asyncio.CancelledError, Exception):
                    pass


async def bot(runner_args: RunnerArguments):
    """Server entrypoint. Only Daily WebRTC sessions are supported; any
    other transport is a no-op."""
    if not isinstance(runner_args, DailyRunnerArguments):
        return
    await run_in_app_bot(runner_args)


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
