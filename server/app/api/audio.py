"""Authenticated Deskbot audio WebSocket endpoint with multilingual STT and Groq intelligence."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
import traceback
from uuid import UUID, uuid4

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from app.api.audio_schemas import (
    AudioCompletedMessage,
    AudioErrorMessage,
    AudioFinishMessage,
    AudioPartialMessage,
    AudioProtocolError,
    AudioStartMessage,
    AudioStartedMessage,
    AudioTranscriptionMessage,
    ServerAudioErrorMessage,
    parse_audio_message,
    utc_timestamp,
)
from app.config import Settings
from app.security.device_auth import parse_bearer_header, verify_device_token
from app.services.audio_sessions import AudioSession, AudioSessionManager
from app.services.commands import CommandContext, CommandRouter, create_registry
from app.services.intelligence import (
    IntelligenceProviderFactory,
    IntentResult,
    FunctionExecutionResult,
    get_conversation_context,
)
from app.services.speech import STTProviderFactory
from app.services.tts import TTSProviderFactory

logger = logging.getLogger(__name__)
AUDIO_SUBPROTOCOL = "deskbot.audio.v1"

# Global instances (initialized lazily)
_command_registry = None
_command_router = None
_stt_factory = None
_intelligence_factory = None
_tts_factory = None


def _get_command_router() -> CommandRouter:
    """Get or create the global command router (fallback)."""
    global _command_registry, _command_router
    if _command_router is None:
        _command_registry = create_registry()
        _command_router = CommandRouter(_command_registry)
    return _command_router


def _get_stt_factory(settings: Settings) -> STTProviderFactory:
    """Get or create the global STT provider factory."""
    global _stt_factory
    if _stt_factory is None:
        _stt_factory = STTProviderFactory(settings)
    return _stt_factory


def _get_intelligence_factory(settings: Settings) -> IntelligenceProviderFactory:
    """Get or create the global intelligence provider factory."""
    global _intelligence_factory, _command_registry
    if _intelligence_factory is None:
        if _command_registry is None:
            _command_registry = create_registry()
        _intelligence_factory = IntelligenceProviderFactory(settings, _command_registry)
    return _intelligence_factory


def _get_tts_factory(settings: Settings) -> TTSProviderFactory:
    """Get or create the global TTS provider factory."""
    global _tts_factory
    if _tts_factory is None:
        _tts_factory = TTSProviderFactory(settings)
    return _tts_factory


def _envelope(message_type: str, payload: dict) -> dict:
    return {
        "protocol_version": "1.0",
        "message_id": str(uuid4()),
        "message_type": message_type,
        "timestamp": utc_timestamp(),
        "payload": payload,
    }


async def _send_json(websocket: WebSocket, message: dict, settings) -> bool:
    try:
        await asyncio.wait_for(
            websocket.send_json(message), timeout=settings.websocket_send_timeout_s
        )
        logger.info(
            "event=audio.response_sent device_id=%s message_type=%s",
            message.get("payload", {}).get("device_id", "unknown"),
            message.get("message_type", "unknown"),
        )
        return True
    except (asyncio.TimeoutError, RuntimeError, OSError):
        logger.warning("Failed to send audio response")
        return False


async def _send_binary(websocket: WebSocket, data: bytes, settings) -> bool:
    """Send binary audio data over WebSocket."""
    try:
        await asyncio.wait_for(
            websocket.send_bytes(data), timeout=settings.websocket_send_timeout_s
        )
        return True
    except (asyncio.TimeoutError, RuntimeError, OSError) as exc:
        logger.warning("Failed to send binary audio: %s", exc)
        return False


async def _close(websocket: WebSocket, code: int, reason: str) -> None:
    try:
        await websocket.close(code=code, reason=reason[:120])
    except (RuntimeError, OSError):
        pass


async def _process_with_intelligence(
    websocket: WebSocket,
    session: AudioSession,
    settings: Settings,
    transcription_text: str,
    detected_language: str,
    language_confidence: float | None,
    provider: str,
    provider_language: str | None,
    provider_language_prob: float | None,
    transcription_metadata: dict | None,
) -> None:
    """
    Process transcription through Groq intelligence layer.
    
    Flow:
    1. Create intelligence context
    2. Recognize intent via Groq (or fallback router)
    3. If function intent: execute function, generate response via Groq
    4. If conversational: generate conversational response via Groq
    5. Send command.completed with response
    """
    try:
        intelligence_factory = _get_intelligence_factory(settings)
        intelligence_provider = await intelligence_factory.get_provider()
        
        # Create intelligence context
        context = IntelligentContext(
            device_id=session.device_id,
            recording_id=session.recording_id,
            transcript=transcription_text,
            detected_language=detected_language,
            language_confidence=language_confidence,
            timezone=settings.timezone,
            location=settings.default_location,
        )
        
        # Try Groq first if available
        if intelligence_provider is not None:
            try:
                await _process_with_groq(
                    websocket, session, settings, context, intelligence_provider,
                    provider, provider_language, provider_language_prob, transcription_metadata
                )
                return
            except Exception as exc:
                logger.warning(
                    "event=intelligence.groq_failed device_id=%s recording_id=%s error=%s",
                    session.device_id, session.recording_id, exc
                )
                # Fall through to deterministic router
        
        # Fallback to deterministic router
        await _process_with_fallback_router(
            websocket, session, settings, context,
            provider, provider_language, provider_language_prob, transcription_metadata
        )
        
    except Exception as exc:
        logger.warning(
            "event=command.processing_failed device_id=%s recording_id=%s error=%s traceback=%s",
            session.device_id, session.recording_id, exc, traceback.format_exc()
        )


async def _process_with_groq(
    websocket: WebSocket,
    session: AudioSession,
    settings: Settings,
    context: "IntelligentContext",
    intelligence_provider,
    provider: str,
    provider_language: str | None,
    provider_language_prob: float | None,
    transcription_metadata: dict | None,
) -> None:
    """Process using Groq intelligence provider."""
    from app.services.intelligence import FunctionExecutionResult
    
    logger.info(
        "event=intelligence.groq_recognize_intent device_id=%s recording_id=%s transcript='%s'",
        session.device_id, session.recording_id, context.transcript
    )
    
    # Step 1: Recognize intent
    intent_result = await intelligence_provider.recognize_intent(context)
    
    logger.info(
        "event=intelligence.groq_intent_matched device_id=%s recording_id=%s intent=%s language=%s confidence=%.2f",
        session.device_id, session.recording_id, intent_result.intent, intent_result.response_language, intent_result.confidence
    )
    
    # Step 2: Execute function if intent is not "none"
    if intent_result.intent != "none":
        # Create command context for execution
        command_context = CommandContext(
            device_id=context.device_id,
            recording_id=UUID(context.recording_id),
            transcript=context.transcript,
            language=settings.default_language,
            detected_language=intent_result.response_language,
            language_confidence=context.language_confidence,
            timezone=context.timezone,
            location=context.location,
        )
        
        # Execute via tool registry (which uses command registry)
        from app.services.intelligence import get_conversation_context
        tool_registry = intelligence_provider._tool_registry  # Access internal tool registry
        
        command_result = await tool_registry.execute_function(
            intent_result.intent,
            intent_result.arguments,
            command_context,
        )
        
        logger.info(
            "event=intelligence.groq_function_executed device_id=%s recording_id=%s intent=%s recognized=%s",
            session.device_id, session.recording_id, intent_result.intent, command_result.recognized
        )
        
        # Build execution result for response generation
        execution_result = FunctionExecutionResult(
            intent=intent_result.intent,
            arguments=intent_result.arguments,
            result=command_result.metadata or {},
            success=command_result.recognized,
        )
        
        # Step 3: Generate natural language response via Groq
        response_result = await intelligence_provider.generate_response(context, execution_result)
        
        response_text = response_result.response_text
        response_language = response_result.response_language
        recognized = command_result.recognized
        
    else:
        # Conversational response (no function)
        logger.info(
            "event=intelligence.groq_conversational device_id=%s recording_id=%s",
            session.device_id, session.recording_id
        )
        
        response_result = await intelligence_provider.generate_conversational_response(context)
        
        response_text = response_result.response_text
        response_language = response_result.response_language
        recognized = False
    
    # Record in conversation history
    conv_context = get_conversation_context()
    conv_context.add_turn(
        device_id=session.device_id,
        recording_id=session.recording_id,
        transcript=context.transcript,
        detected_language=context.detected_language,
        intent=intent_result.intent if intent_result.intent != "none" else None,
        recognized=recognized,
        response_text=response_text,
        response_language=response_language,
        timestamp=time.time(),
    )
    
    # Send command.completed
    # Only include metadata fields that the ESP32 firmware actually uses or needs.
    # Exclude transcription_metadata (Whisper internals) which can be very large.
    command_metadata = {
        "provider": provider,
        "provider_language": provider_language,
        "provider_language_probability": provider_language_prob,
        "intelligence_provider": "groq",
        "intelligence_model": intelligence_provider.info.model,
    }
    
    command_envelope = _envelope(
        "command.completed",
        {
            "recording_id": session.recording_id,
            "intent": intent_result.intent if intent_result.intent != "none" else "unknown",
            "recognized": recognized,
            "detected_language": response_language,
            "response_language": response_language,
            "response_text": response_text,
            "metadata": command_metadata,
        },
    )
    await _send_json(websocket, command_envelope, settings)
    
    logger.info(
        "event=command.completed device_id=%s intent=%s recognized=%s language=%s intelligence=groq",
        session.device_id, intent_result.intent, recognized, response_language
    )

    # Stream TTS audio if enabled
    if response_text and recognized:
        await _stream_tts_audio(
            websocket, session, settings,
            response_text, response_language
        )


async def _process_with_fallback_router(
    websocket: WebSocket,
    session: AudioSession,
    settings: Settings,
    context: "IntelligentContext",
    provider: str,
    provider_language: str | None,
    provider_language_prob: float | None,
    transcription_metadata: dict | None,
) -> None:
    """Fallback to deterministic keyword-based router."""
    logger.info(
        "event=intelligence.fallback_router device_id=%s recording_id=%s",
        session.device_id, session.recording_id
    )
    
    router = _get_command_router()
    command_context = CommandContext(
        device_id=context.device_id,
        recording_id=UUID(context.recording_id),
        transcript=context.transcript,
        language=settings.default_language,
        detected_language=context.detected_language,
        language_confidence=context.language_confidence,
        timezone=context.timezone,
        location=context.location,
    )
    
    intent = await router.route(command_context)
    result = await _command_registry.execute(intent.name, command_context)
    
    # Record in conversation history
    conv_context = get_conversation_context()
    conv_context.add_turn(
        device_id=session.device_id,
        recording_id=session.recording_id,
        transcript=context.transcript,
        detected_language=context.detected_language,
        intent=intent.name if intent.name != "unknown" else None,
        recognized=result.recognized,
        response_text=result.response_text,
        response_language=result.response_language,
        timestamp=time.time(),
    )
    
    command_metadata = {
        "provider": provider,
        "provider_language": provider_language,
        "provider_language_probability": provider_language_prob,
        "intelligence_provider": "fallback_router",
    }
    # Exclude transcription_metadata (Whisper internals) which can be very large
    # and is not used by the ESP32 firmware.
    
    command_envelope = _envelope(
        "command.completed",
        {
            "recording_id": session.recording_id,
            "intent": result.intent.name,
            "recognized": result.recognized,
            "detected_language": result.response_language,
            "response_language": result.response_language,
            "response_text": result.response_text,
            "metadata": command_metadata,
        },
    )
    await _send_json(websocket, command_envelope, settings)
    
    logger.info(
        "event=command.completed device_id=%s intent=%s recognized=%s language=%s intelligence=fallback",
        session.device_id, result.intent.name, result.recognized, result.response_language
    )

    # Stream TTS audio if enabled
    if result.response_text and result.recognized:
        await _stream_tts_audio(
            websocket, session, settings,
            result.response_text, result.response_language
        )


async def _stream_tts_audio(
    websocket: WebSocket,
    session: AudioSession,
    settings: Settings,
    text: str,
    language: str,
) -> None:
    """Stream TTS audio to the ESP32 via binary WebSocket frames."""
    if not settings.tts_enabled:
        logger.debug("TTS disabled, skipping audio playback")
        return

    try:
        tts_factory = _get_tts_factory(settings)
        tts_provider = await tts_factory.get_provider()
        if tts_provider is None:
            logger.info("No TTS provider available, skipping audio playback")
            return

        logger.info(
            "event=tts.stream_start device_id=%s recording_id=%s language=%s text_length=%d",
            session.device_id, session.recording_id, language, len(text)
        )

        # Synthesize speech
        tts_result = await tts_provider.synthesize(text, language)
        
        if not tts_result.audio_data:
            logger.warning("TTS returned empty audio data")
            return

        # Send tts.start metadata
        start_envelope = _envelope(
            "tts.start",
            {
                "recording_id": session.recording_id,
                "sample_rate": tts_result.sample_rate,
                "channels": tts_result.channels,
                "bits_per_sample": tts_result.bits_per_sample,
                "format": tts_result.format,
                "duration_seconds": tts_result.duration_seconds,
            },
        )
        await _send_json(websocket, start_envelope, settings)

        # Stream audio in chunks
        chunk_size = settings.audio_max_chunk_bytes
        audio_data = tts_result.audio_data
        total_bytes = len(audio_data)
        bytes_sent = 0
        chunk_count = 0

        for i in range(0, total_bytes, chunk_size):
            chunk = audio_data[i:i + chunk_size]
            success = await _send_binary(websocket, chunk, settings)
            if not success:
                logger.error("Failed to send TTS audio chunk %d", chunk_count)
                break
            bytes_sent += len(chunk)
            chunk_count += 1
            # Small delay to prevent overwhelming the ESP32
            await asyncio.sleep(0.005)

        # Send tts.end metadata
        end_envelope = _envelope(
            "tts.end",
            {
                "recording_id": session.recording_id,
                "total_bytes": bytes_sent,
                "chunks": chunk_count,
            },
        )
        await _send_json(websocket, end_envelope, settings)

        logger.info(
            "event=tts.stream_completed device_id=%s recording_id=%s bytes=%d chunks=%d",
            session.device_id, session.recording_id, bytes_sent, chunk_count
        )

    except Exception as exc:
        logger.error(
            "event=tts.stream_failed device_id=%s recording_id=%s error=%s",
            session.device_id, session.recording_id, exc
        )
        # Notify ESP32 of TTS failure
        error_envelope = _envelope(
            "tts.error",
            {
                "recording_id": session.recording_id,
                "code": "tts_failed",
                "message": str(exc),
            },
        )
        await _send_json(websocket, error_envelope, settings)


class IntelligentContext:
    """Context for intelligence provider (matches IntelligenceContext interface)."""
    
    def __init__(
        self,
        device_id: str,
        recording_id: str,
        transcript: str,
        detected_language: str,
        language_confidence: float | None,
        timezone: str,
        location: str,
    ):
        self.device_id = device_id
        self.recording_id = recording_id
        self.transcript = transcript
        self.detected_language = detected_language
        self.language_confidence = language_confidence
        self.timezone = timezone
        self.location = location
    
    @property
    def available_functions(self):
        """Get available functions from the tool registry."""
        # This will be populated by the intelligence provider
        return []


async def audio_socket(websocket: WebSocket) -> None:
    settings = websocket.app.state.settings
    manager: AudioSessionManager = websocket.app.state.audio_sessions
    offered = websocket.scope.get("subprotocols", [])
    if AUDIO_SUBPROTOCOL not in offered:
        await _close(websocket, 1008, "required subprotocol deskbot.audio.v1")
        return
    token = parse_bearer_header(websocket.headers.get("authorization"))
    authenticated_device_id = verify_device_token(token, settings.device_token_hashes)
    if authenticated_device_id is None:
        await _close(websocket, 1008, "authentication failed")
        return

    await websocket.accept(subprotocol=AUDIO_SUBPROTOCOL)
    logger.info("event=audio.accepted device_id=%s", authenticated_device_id)

    session: AudioSession | None = None
    recording_started = False
    audio_chunks: list[bytes] = []  # Store chunks for Whisper transcription

    try:
        # Get STT provider
        stt_factory = _get_stt_factory(settings)
        stt_provider = await stt_factory.get_provider()
        provider_info = stt_provider.info
        logger.info(
            "event=stt.provider_selected device_id=%s provider=%s model=%s streaming=%s",
            authenticated_device_id,
            provider_info.name,
            provider_info.model,
            provider_info.streaming,
        )

        while await manager.is_current(session) if session else True:
            try:
                frame = await asyncio.wait_for(
                    websocket.receive(), timeout=settings.audio_chunk_timeout_s
                )
            except asyncio.TimeoutError:
                if session and not session.finished:
                    logger.info("event=audio.chunk_timeout device_id=%s", session.device_id)
                    await _close(websocket, 1001, "audio chunk timeout")
                break

            if frame.get("type") == "websocket.disconnect":
                logger.info(
                    "event=audio.client_disconnected device_id=%s code=%s",
                    session.device_id if session else authenticated_device_id,
                    frame.get("code", "unknown"),
                )
                break

            if frame.get("bytes") is not None:
                if not session or not recording_started or session.finished:
                    await _close(websocket, 1008, "binary frame not allowed in current state")
                    break
                chunk = frame["bytes"]
                if len(chunk) == 0:
                    await _close(websocket, 1008, "empty audio chunk")
                    break
                if len(chunk) % 2 != 0:
                    await _close(websocket, 1008, "odd-sized PCM chunk")
                    break
                if len(chunk) > settings.audio_max_chunk_bytes:
                    await _close(websocket, 1009, "audio chunk too large")
                    break

                elapsed_ms = int((time.monotonic() - session.started_at) * 1000)
                if elapsed_ms > settings.audio_max_duration_s * 1000:
                    await _close(websocket, 1008, "recording too long")
                    break

                # Store chunk for later transcription (needed for Whisper)
                audio_chunks.append(chunk)

                # If using Vosk with streaming, process partial results
                if provider_info.streaming and session.recognizer is not None:
                    try:
                        is_final = session.recognizer.AcceptWaveform(chunk)
                        if is_final:
                            result = json.loads(session.recognizer.Result())
                            text = result.get("text", "")
                            if text:
                                await _send_json(
                                    websocket,
                                    _envelope(
                                        "audio.transcription",
                                        {
                                            "recording_id": session.recording_id,
                                            "text": text,
                                            "is_final": True,
                                        },
                                    ),
                                    settings,
                                )
                        else:
                            partial = json.loads(session.recognizer.PartialResult())
                            partial_text = partial.get("partial", "")
                            if partial_text:
                                await _send_json(
                                    websocket,
                                    _envelope(
                                        "audio.partial",
                                        {
                                            "recording_id": session.recording_id,
                                            "text": partial_text,
                                            "is_final": False,
                                        },
                                    ),
                                    settings,
                                )
                    except Exception as exc:
                        logger.warning(
                            "event=vosk.processing_failed device_id=%s error=%s",
                            session.device_id,
                            exc,
                        )

                session.total_chunks += 1
                session.total_bytes += len(chunk)
                continue

            text = frame.get("text", "")
            if not text:
                await _close(websocket, 1008, "empty message")
                break

            try:
                message = parse_audio_message(text, settings.websocket_max_message_bytes)
            except AudioProtocolError as exc:
                logger.warning(
                    "event=audio.protocol_rejected device_id=%s code=%s",
                    session.device_id if session else authenticated_device_id,
                    exc.code,
                )
                error_msg = _envelope(
                    "server.audio.error",
                    {
                        "code": exc.code,
                        "message": exc.code.replace("_", " "),
                        "related_message_id": None,
                        "retryable": not exc.fatal,
                    },
                )
                await _send_json(websocket, error_msg, settings)
                if exc.fatal:
                    await _close(websocket, exc.close_code, exc.code)
                break

            if isinstance(message, AudioStartMessage):
                if session and recording_started and not session.finished:
                    await _close(websocket, 1008, "recording already in progress")
                    break

                if (
                    message.payload.sample_rate != settings.audio_sample_rate
                    or message.payload.channels != settings.audio_channels
                    or message.payload.format != settings.audio_format
                ):
                    await _close(websocket, 1008, "invalid audio format")
                    break

                # Create recognizer for streaming providers (Vosk)
                recognizer = None
                if provider_info.streaming:
                    # For Vosk, create recognizer
                    try:
                        from vosk import KaldiRecognizer, Model
                        if _stt_factory._provider is not None:
                            # Access the Vosk provider's model
                            vosk_provider = _stt_factory._provider
                            if hasattr(vosk_provider, '_model') and vosk_provider._model:
                                recognizer = KaldiRecognizer(vosk_provider._model, message.payload.sample_rate)
                    except Exception as exc:
                        logger.warning("Failed to create streaming recognizer: %s", exc)

                session = await manager.start_recording(
                    device_id=authenticated_device_id,
                    recording_id=str(message.payload.recording_id),
                    websocket=websocket,
                    sample_rate=message.payload.sample_rate,
                    channels=message.payload.channels,
                    format=message.payload.format,
                )
                if recognizer is not None:
                    session.recognizer = recognizer
                recording_started = True
                audio_chunks = []  # Reset chunk buffer for new recording
                logger.info(
                    "event=audio.started device_id=%s recording_id=%s",
                    session.device_id,
                    session.recording_id,
                )

                await _send_json(
                    websocket,
                    _envelope(
                        "audio.started",
                        {
                            "recording_id": session.recording_id,
                            "sample_rate": session.sample_rate,
                            "channels": session.channels,
                            "format": session.format,
                            "max_duration_s": settings.audio_max_duration_s,
                            "max_chunk_bytes": settings.audio_max_chunk_bytes,
                            "stt_provider": provider_info.name,
                            "stt_model": provider_info.model,
                            "streaming": provider_info.streaming,
                        },
                    ),
                    settings,
                )
                continue

            if not session:
                await _close(websocket, 1008, "recording not started")
                break

            if str(message.payload.recording_id) != session.recording_id:
                await _close(websocket, 1008, "recording ID mismatch")
                break

            if isinstance(message, AudioFinishMessage):
                if session.finished:
                    await _close(websocket, 1008, "recording already finished")
                    break

                finished_ok = await manager.finish_recording(session, str(message.payload.recording_id))
                if not finished_ok:
                    await _close(websocket, 1008, "recording ID mismatch or already finished")
                    break

                # Combine all audio chunks for transcription
                combined_audio = b"".join(audio_chunks)
                duration_ms = int((time.monotonic() - session.started_at) * 1000)

                logger.info(
                    "event=audio.completed device_id=%s recording_id=%s chunks=%d bytes=%d duration_ms=%d",
                    session.device_id,
                    session.recording_id,
                    session.total_chunks,
                    session.total_bytes,
                    duration_ms,
                )

                await _send_json(
                    websocket,
                    _envelope(
                        "audio.completed",
                        {
                            "recording_id": session.recording_id,
                            "total_chunks": session.total_chunks,
                            "total_bytes": session.total_bytes,
                            "duration_ms": duration_ms,
                        },
                    ),
                    settings,
                )

                # Perform authoritative transcription using the selected STT provider
                transcription_text = ""
                detected_language = settings.default_language.split("-")[0]  # e.g., "hi" from "hi-IN"
                language_confidence = None
                transcription_metadata = None

                if combined_audio:
                    try:
                        transcription_result = await stt_provider.transcribe(
                            combined_audio,
                            session.sample_rate,
                            language=None,  # Auto-detect
                        )
                        transcription_text = transcription_result.text
                        detected_language = transcription_result.language
                        language_confidence = transcription_result.language_probability
                        transcription_metadata = transcription_result.metadata
                        logger.info(
                            "event=stt.transcription device_id=%s recording_id=%s provider=%s text='%s' language=%s confidence=%s",
                            session.device_id,
                            session.recording_id,
                            provider_info.name,
                            transcription_text,
                            detected_language,
                            language_confidence,
                        )
                    except Exception as exc:
                        logger.warning(
                            "event=stt.transcription_failed device_id=%s recording_id=%s error=%s traceback=%s",
                            session.device_id,
                            session.recording_id,
                            exc,
                            traceback.format_exc(),
                        )

                # Send final transcription
                if transcription_text:
                    # Extract provider language info from transcription result metadata
                    provider = provider_info.name
                    provider_language = transcription_metadata.get("whisper_language") if transcription_metadata else None
                    provider_language_prob = transcription_metadata.get("whisper_language_probability") if transcription_metadata else None

                    await _send_json(
                        websocket,
                        _envelope(
                            "audio.transcription",
                            {
                                "recording_id": session.recording_id,
                                "text": transcription_text,
                                "is_final": True,
                                "language": detected_language,
                                "language_confidence": language_confidence,
                                "provider": provider,
                                "provider_language": provider_language,
                                "provider_language_probability": provider_language_prob,
                            },
                        ),
                        settings,
                    )

                    # Process command through intelligence layer (Groq + fallback)
                    await _process_with_intelligence(
                        websocket, session, settings,
                        transcription_text, detected_language, language_confidence,
                        provider, provider_language, provider_language_prob,
                        transcription_metadata
                    )
                else:
                    # Empty transcription - send unknown command response via fallback
                    context = IntelligentContext(
                        device_id=session.device_id,
                        recording_id=session.recording_id,
                        transcript="",
                        detected_language=detected_language,
                        language_confidence=language_confidence,
                        timezone=settings.timezone,
                        location=settings.default_location,
                    )
                    await _process_with_fallback_router(
                        websocket, session, settings, context,
                        provider_info.name, None, None, None
                    )
                continue

            await _close(websocket, 1008, "message direction is invalid")
            break

    except WebSocketDisconnect as exc:
        logger.info(
            "event=audio.client_disconnected device_id=%s code=%s",
            session.device_id if session else authenticated_device_id,
            exc.code,
        )
    except asyncio.CancelledError:
        raise
    except (asyncio.TimeoutError, OSError, RuntimeError) as exc:
        logger.info(
            "event=audio.connection_ended device_id=%s error_type=%s",
            session.device_id if session else authenticated_device_id,
            type(exc).__name__,
        )
    finally:
        if session is not None:
            logger.info("event=audio.session_cleanup_started device_id=%s", session.device_id)
            await manager.remove(session)
            logger.info("event=audio.session_cleanup device_id=%s", session.device_id)