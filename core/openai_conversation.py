"""OpenAI-compatible continuous conversation controller."""

import asyncio

from core.openai import OpenAIManager
from core.external_conversation import ExternalConversationController
from core.services.streaming_playback import OrderedPlaybackQueue


class OpenAIConversationController(ExternalConversationController):
    """Manages multi-turn conversation for OpenAI-compatible services."""

    CONFIG_PREFIX = "openai"
    BACKEND_NAME = "OpenAI"
    LOG_MODULE = "OpenAI Conv"
    WAKEUP_SOURCE = "openai"
    MANAGER = OpenAIManager

    def __init__(self):
        super().__init__()
        self._turn_tools = None
        self._turn_voice = None
        self._feedback_succeeded = False
        self._stream_queue = None
        self._streaming_played = False

    @property
    def exit_keywords(self):
        return list(dict.fromkeys(super().exit_keywords + ["拜拜","再见","没事了","不用了"]))

    def stop(self):
        # WakeupSessionManager normally cancels the owning conversation task;
        # cancel the sentence queue here as well for direct controller users.
        if self._stream_queue is not None:
            asyncio.create_task(self._stream_queue.cancel())
        super().stop()

    async def start(self):
        try:
            await super().start()
        finally:
            if self._turn_tools:
                await self._turn_tools.finish_feedback(False)
                from core.ref import get_app
                app = get_app()
                if app is not None:
                    app.conversation_tools.pop(self._turn_tools.session_key, None)

    async def _request_response(self, text):
        from core.ref import get_app
        from core.services.conversation_tools import ConversationTools
        app = get_app()
        if self._cfg("tools_enabled", False):
            if app is None:
                raise RuntimeError("受控工具需要 MainApp 审计服务")
            record_tools = getattr(app, "record_tools", None)
            if record_tools is None:
                ambient = getattr(app, "_ambient", None)
                record_tools = getattr(ambient, "tools", None)
            self._turn_tools = ConversationTools(
                app.native_bridge,
                self.backend._session_key,
                home=app.home_assistant,
                records=record_tools,
                user_text=getattr(self, "_current_user_text", text),
            )
            app.conversation_tools[self._turn_tools.session_key] = self._turn_tools
        else:
            self._turn_tools = None
        if self.backend._chat_streaming_enabled:
            spoken = []
            import open_xiaoai_server

            # Reuse the base controller token so a wake interruption can stop
            # the currently active sentence as well as canceling the queue.
            self._playback_token = open_xiaoai_server.begin_playback_session()
            self._feedback_succeeded = True

            async def consume(sentence):
                spoken.append(sentence)
                succeeded = await self.backend._play_response_with_tts(
                    sentence,
                    playback_token=self._playback_token,
                    voice_directive=self._turn_voice,
                )
                if succeeded is False:
                    self._feedback_succeeded = False

            async def on_sentence(sentence, directive=None):
                if directive is not None:
                    self._turn_voice = directive
                await self._stream_queue.put(sentence)

            self._stream_queue = OrderedPlaybackQueue(consume)
            await self._stream_queue.start()
            try:
                response, self._turn_voice = await self.backend.chat_turn_stream(
                    text,
                    None if self.backend._protocol == 'hermes_native' else self._turn_tools,
                    on_sentence=on_sentence,
                )
                await self._stream_queue.finish()
                self._streaming_played = bool(spoken)
            except asyncio.CancelledError:
                await self._stream_queue.cancel()
                if self._playback_token is not None:
                    open_xiaoai_server.stop_tts_playback(self._playback_token)
                raise
            except Exception:
                await self._stream_queue.cancel()
                if self._playback_token is not None:
                    open_xiaoai_server.stop_tts_playback(self._playback_token)
                raise
            finally:
                self._stream_queue = None
                self._playback_token = None
            self._feedback_succeeded = self._feedback_succeeded and bool(spoken)
        else:
            response, self._turn_voice = await self.backend.chat_turn(
                text, None if self.backend._protocol == 'hermes_native' else self._turn_tools)
        if response and self._turn_tools and self.backend._protocol == 'hermes_native':
            response = self._turn_tools.feedback(response)
        if not response and self._turn_tools:
            await self._turn_tools.finish_feedback(False)
        return response

    async def _play_tts(self, text):
        if self._streaming_played:
            # The queue already consumed each sentence in order while Hermes
            # was generating.  Do not speak the final aggregate a second time.
            self._streaming_played = False
            return
        import open_xiaoai_server
        self._playback_token = open_xiaoai_server.begin_playback_session()
        try:
            self._feedback_succeeded = False
            succeeded = await self.backend._play_response_with_tts(text, playback_token=self._playback_token,
                                                                  voice_directive=self._turn_voice)
            self._feedback_succeeded = succeeded is not False and self.active
        finally:
            self._playback_token = None

    async def _finish_turn_feedback(self) -> bool:
        if self._turn_tools:
            if self._feedback_succeeded and self._turn_tools.has_deferred_playback:
                # 音乐提交前恢复采音；提交后绝不再插入录音/提示动作。
                # 在等待设备回执前标记交接，取消后也不能干扰可能已开始的音乐。
                await self._start_recording()
                self.yielded_playback = True
            try:
                return await self._turn_tools.finish_feedback(self._feedback_succeeded)
            finally:
                from core.ref import get_app
                app = get_app()
                if app is not None:
                    app.conversation_tools.pop(self._turn_tools.session_key, None)
        return False
