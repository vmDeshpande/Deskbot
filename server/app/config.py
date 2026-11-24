"""Application settings loaded from environment variables and server/.env."""

from functools import lru_cache
from pathlib import Path
from uuid import UUID

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the Deskbot API."""

    service_name: str = "Deskbot API"
    version: str = "1.0.0"
    environment: str = "development"
    host: str = "0.0.0.0"
    port: int = Field(default=8000, ge=1, le=65535)
    websocket_path: str = "/ws/device"
    device_token_hashes: dict[str, str] = Field(default_factory=dict, repr=False)
    websocket_max_message_bytes: int = Field(default=4096, ge=256, le=65536)
    websocket_outbound_queue_size: int = Field(default=32, ge=1, le=256)
    websocket_send_timeout_s: float = Field(default=5.0, gt=0, le=60)
    device_registration_timeout_s: float = Field(default=10.0, gt=0, le=120)
    device_heartbeat_interval_s: int = Field(default=30, ge=1, le=300)
    device_heartbeat_timeout_s: int = Field(default=90, ge=2, le=900)
    websocket_max_messages_per_minute: int = Field(default=120, ge=1, le=10000)
    websocket_max_protocol_strikes: int = Field(default=3, ge=1, le=20)
    audio_websocket_path: str = "/ws/audio"
    audio_max_chunk_bytes: int = Field(default=3200, ge=320, le=32000)
    audio_max_duration_s: int = Field(default=30, ge=1, le=300)
    audio_sample_rate: int = Field(default=16000, ge=8000, le=48000)
    audio_channels: int = Field(default=1, ge=1, le=2)
    audio_format: str = "pcm_s16le"
    audio_chunk_timeout_s: float = Field(default=5.0, gt=0, le=60)
    audio_min_duration_s: float = Field(default=0.5, ge=0.1, le=10.0, description="Minimum audio duration for transcription")
    vosk_model_path: str = Field(default="", description="Path to Vosk speech recognition model directory")
    default_language: str = Field(default="hi-IN", description="Default language for responses")
    timezone: str = Field(default="Asia/Kolkata", description="Default timezone for datetime commands")
    default_location: str = Field(default="Nashik, Maharashtra, India", description="Default location for weather/location features")
    stt_provider: str = Field(default="vosk", description="STT provider: vosk or whisper")
    whisper_model: str = Field(default="small", description="Whisper model size: tiny, base, small, medium, large")
    whisper_device: str = Field(default="cpu", description="Whisper device: cpu or cuda")
    whisper_compute_type: str = Field(default="int8", description="Whisper compute type: int8, int8_float16, float16, float32")
    language_mode: str = Field(default="auto", description="Language detection mode: auto")
    supported_languages: str = Field(default="en,hi,mr", description="Comma-separated list of supported language codes")
    whisper_no_speech_threshold: float = Field(default=0.6, ge=0.0, le=1.0, description="Whisper no_speech_threshold")
    whisper_log_prob_threshold: float = Field(default=-1.0, le=0.0, description="Whisper log_prob_threshold")
    whisper_compression_ratio_threshold: float = Field(default=2.4, ge=1.0, le=10.0, description="Whisper compression_ratio_threshold")
    whisper_vad_filter: bool = Field(default=True, description="Enable Whisper VAD filtering")
    whisper_vad_parameters: dict = Field(default_factory=lambda: {"min_silence_duration_ms": 500}, description="Whisper VAD parameters")

    # Groq Intelligence Provider
    intelligence_provider: str = Field(default="groq", description="Intelligence provider: groq")
    intelligence_enabled: bool = Field(default=True, description="Enable intelligence provider")
    groq_api_key: str = Field(default="", description="Groq API key", repr=False)
    groq_model: str = Field(default="llama-3.3-70b-versatile", description="Groq model name")

    # Google Cloud TTS Provider
    tts_enabled: bool = Field(default=False, description="Enable TTS playback")
    tts_provider: str = Field(default="google", description="TTS provider: google")
    tts_language: str = Field(default="en-IN", description="Default TTS language (BCP-47)")
    tts_voice_en: str = Field(default="", description="English voice name (overrides default)")
    tts_voice_hi: str = Field(default="", description="Hindi voice name (overrides default)")
    tts_voice_mr: str = Field(default="", description="Marathi voice name (overrides default)")
    tts_sample_rate: int = Field(default=16000, ge=8000, le=48000, description="TTS output sample rate")
    tts_audio_format: str = Field(default="pcm_s16le", description="TTS output audio format")

    model_config = SettingsConfigDict(
        env_prefix="DESKBOT_",
        env_file=Path(__file__).resolve().parents[1] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("host must not be empty")
        return value

    @field_validator("websocket_path")
    @classmethod
    def validate_websocket_path(cls, value: str) -> str:
        if not value.startswith("/") or value.startswith("//") or "?" in value or "#" in value:
            raise ValueError("websocket_path must be an absolute URL path without query or fragment")
        return value

    @field_validator("device_token_hashes")
    @classmethod
    def validate_device_token_hashes(cls, values: dict[str, str]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for device_id, token_hash in values.items():
            canonical_id = str(UUID(device_id))
            if len(token_hash) != 64 or any(char not in "0123456789abcdefABCDEF" for char in token_hash):
                raise ValueError("device token verifiers must be 64-character SHA-256 hex digests")
            if canonical_id in normalized:
                raise ValueError("duplicate device UUID in device token configuration")
            if token_hash.lower() in normalized.values():
                raise ValueError("a device token verifier must be bound to only one UUID")
            normalized[canonical_id] = token_hash.lower()
        return normalized


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings."""

    return Settings()
