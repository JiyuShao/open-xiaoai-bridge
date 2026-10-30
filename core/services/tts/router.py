"""Shared TTS provider routing and playback for all conversation backends."""

import os
import inspect
import tempfile
from typing import Any, Callable

import open_xiaoai_server

from core.services.tts.doubao import DoubaoTTS
from core.services.tts.mlx_audio import MLXAudioTTS
from core.services.tts.openai import PLAYBACK_SUPPORTED_FORMATS, OpenAITTS
from core.services.tts.voice import VoiceDirective
from core.utils.config import ConfigManager
from core.utils.logger import logger


class TTSRouter:
    """Resolve a backend's TTS settings and play the generated response."""

    XIAOAI_TTS_SPEAKER = "xiaoai"
    SUPPORTED_PROVIDERS = frozenset(("xiaoai", "doubao", "openai", "mlx_audio"))

    @classmethod
    def resolve_provider(
        cls,
        configured_provider: str | None,
        tts_speaker: str | None,
    ) -> str:
        """Resolve an explicit provider while preserving legacy selection."""
        provider = str(configured_provider or "").strip().lower()
        if provider in cls.SUPPORTED_PROVIDERS:
            return provider
        if provider:
            logger.warning(
                f"Unknown tts_provider={configured_provider!r}; "
                "using legacy speaker-based selection"
            )
        return "xiaoai" if tts_speaker == cls.XIAOAI_TTS_SPEAKER else "doubao"

    @classmethod
    async def play(
        cls,
        text: str,
        *,
        configured_provider: str | None,
        tts_speaker: str | None,
        tts_speed: float = 1.0,
        playback_token: int | None = None,
        voice_directive: VoiceDirective | None = None,
        output_mode: str = "play",
        request_sink: Callable[[dict[str, Any]], Any] | None = None,
        log_prefix: str = "TTS",
    ) -> bool:
        """Synthesize and play text, falling back to XiaoAI native TTS."""
        provider = cls.resolve_provider(configured_provider, tts_speaker)
        try:
            if output_mode not in ("play", "record"):
                raise ValueError(f"Unknown TTS output mode: {output_mode}")
            if output_mode == "record":
                await cls._record_request(
                    text,
                    provider=provider,
                    tts_speaker=tts_speaker,
                    tts_speed=tts_speed,
                    voice_directive=voice_directive,
                    request_sink=request_sink,
                )
                return True
            if provider == "xiaoai":
                from core.ref import get_speaker

                speaker = get_speaker()
                if speaker:
                    return bool(await speaker.play(text=text, blocking=True))
                return False

            if provider == "doubao":
                await cls._play_doubao(
                    text,
                    tts_speaker=tts_speaker,
                    tts_speed=tts_speed,
                    playback_token=playback_token,
                    voice_directive=voice_directive,
                )
                return True

            await cls._play_openai_compatible(
                text,
                provider=provider,
                tts_speaker=tts_speaker,
            )
            return True
        except Exception as exc:
            logger.error(
                f"[{log_prefix}] Error playing response with TTS: "
                f"{type(exc).__name__}: {exc}"
            )
            if output_mode == "record":
                return False
            return await cls._fallback_to_xiaoai(
                text,
                log_prefix=log_prefix,
                provider=provider,
                reason=type(exc).__name__,
                request_sink=request_sink,
            )

    @classmethod
    async def _record_request(
        cls,
        text: str,
        *,
        provider: str,
        tts_speaker: str | None,
        tts_speed: float,
        voice_directive: VoiceDirective | None,
        request_sink: Callable[[dict[str, Any]], Any] | None,
    ) -> None:
        """记录合成请求，不访问凭据、网络、音箱或降级播放。"""
        recorded_speaker = tts_speaker
        if provider == "doubao" and recorded_speaker in (None, cls.XIAOAI_TTS_SPEAKER):
            tts_config = ConfigManager.instance().get_app_config("tts.doubao", {})
            recorded_speaker = tts_config.get(
                "default_speaker", "zh_female_vv_uranus_bigtts"
            )
        record: dict[str, Any] = {
            "provider": provider,
            "text": text,
            "speaker": recorded_speaker,
            "speed": tts_speed,
        }
        if voice_directive:
            record.update({
                "context_text": voice_directive.context_text,
                "explicit_language": voice_directive.explicit_language,
                "explicit_dialect": voice_directive.explicit_dialect,
                "emotion": voice_directive.emotion,
            })
        if request_sink:
            result = request_sink(record)
            if inspect.isawaitable(result):
                await result
        logger.info(f"[TTS] Recorded silent synthesis request: provider={provider}")

    @classmethod
    async def _play_doubao(
        cls,
        text: str,
        *,
        tts_speaker: str | None,
        tts_speed: float,
        playback_token: int | None,
        voice_directive: VoiceDirective | None,
    ) -> None:
        tts_config = ConfigManager.instance().get_app_config("tts.doubao", {})
        app_id = tts_config.get("app_id")
        access_key = tts_config.get("access_key")
        if not app_id or not access_key:
            raise ValueError("Doubao TTS credentials are not configured")

        speaker_id = (
            tts_speaker
            if tts_speaker and tts_speaker != cls.XIAOAI_TTS_SPEAKER
            else tts_config.get("default_speaker", "zh_female_vv_uranus_bigtts")
        )
        tts = DoubaoTTS(
            app_id=app_id,
            access_key=access_key,
            speaker=speaker_id,
        )
        resolved_format = tts.resolve_audio_format(text)
        voice_kwargs = {
            "explicit_language": (
                voice_directive.explicit_language if voice_directive else None
            ),
            "explicit_dialect": (
                voice_directive.explicit_dialect if voice_directive else None
            ),
            "emotion": voice_directive.emotion if voice_directive else None,
            "context_texts": (
                voice_directive.context_texts() if voice_directive else None
            ),
        }
        if tts_config.get("stream", False):
            await open_xiaoai_server.tts_stream_play(
                text,
                app_id=app_id,
                access_key=access_key,
                resource_id=tts.resource_id,
                speaker=speaker_id,
                speed=tts_speed,
                format=resolved_format,
                sample_rate=24000,
                **voice_kwargs,
                playback_token=playback_token,
            )
        else:
            await open_xiaoai_server.tts_play(
                text,
                app_id=app_id,
                access_key=access_key,
                resource_id=tts.resource_id,
                speaker=speaker_id,
                speed=tts_speed,
                format=resolved_format,
                sample_rate=24000,
                **voice_kwargs,
                playback_token=playback_token,
            )

    @classmethod
    async def _play_openai_compatible(
        cls,
        text: str,
        *,
        provider: str,
        tts_speaker: str | None,
    ) -> None:
        tts_class: type[OpenAITTS]
        if provider == "mlx_audio":
            tts_class = MLXAudioTTS
            config_key = "tts.mlx_audio"
            log_name = "MLX-Audio"
            file_prefix = "mlx_audio_tts_"
        else:
            tts_class = OpenAITTS
            config_key = "tts.openai"
            log_name = "OpenAI"
            file_prefix = "openai_tts_"

        tts_config = ConfigManager.instance().get_app_config(config_key, {})
        voice_override = (
            tts_speaker
            if tts_speaker and tts_speaker != cls.XIAOAI_TTS_SPEAKER
            else None
        )
        tts = tts_class.from_config(tts_config, voice_override=voice_override)
        logger.info(
            f"[{log_name} TTS] Requesting speech: "
            f"url={tts.speech_url}, model={tts.model}, voice={tts.voice}, "
            f"format={tts.response_format}"
        )

        if tts.response_format not in PLAYBACK_SUPPORTED_FORMATS:
            raise ValueError(
                f"response_format={tts.response_format!r} is not supported by "
                "the Bridge playback decoder; use mp3, flac, wav, ogg, or pcm"
            )

        temporary_path: str | None = None
        try:
            audio = await tts.synthesize(text)
            safe_format = "".join(
                character
                for character in tts.response_format.lower()
                if character.isalnum()
            ) or "wav"
            with tempfile.NamedTemporaryFile(
                prefix=file_prefix,
                suffix=f".{safe_format}",
                delete=False,
            ) as audio_file:
                temporary_path = audio_file.name
                audio_file.write(audio)

            from core.ref import get_speaker

            speaker = get_speaker()
            if not speaker:
                raise RuntimeError("Speaker manager is not available")
            played = await speaker.play_server_file(
                file_path=temporary_path,
                blocking=True,
            )
            if played is False:
                raise RuntimeError("SpeakerManager.play_server_file returned false")
        finally:
            if temporary_path:
                try:
                    os.unlink(temporary_path)
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    logger.warning(
                        f"[{log_name} TTS] Failed to remove temporary "
                        f"audio file {temporary_path}: {exc}"
                    )

    @classmethod
    async def _fallback_to_xiaoai(
        cls,
        text: str,
        *,
        log_prefix: str,
        provider: str,
        reason: str,
        request_sink: Callable[[dict[str, Any]], Any] | None = None,
    ) -> bool:
        """记录降级原因后调用原生音色；记录失败不能阻止降级。"""
        if request_sink:
            try:
                record = request_sink({
                    "event": "tts_fallback",
                    "from_provider": provider,
                    "to_provider": cls.XIAOAI_TTS_SPEAKER,
                    "reason": reason,
                })
                if inspect.isawaitable(record):
                    await record
            except Exception as exc:
                logger.warning(
                    f"[{log_prefix}] Failed to record TTS fallback: {type(exc).__name__}"
                )
        try:
            from core.ref import get_speaker

            speaker = get_speaker()
            if speaker:
                return bool(await speaker.play(text=text, blocking=True))
        except Exception as exc:
            logger.error(f"[{log_prefix}] XiaoAI TTS fallback failed: {exc}")
        return False
