"""OpenAI-compatible continuous conversation controller."""

from core.openai import OpenAIManager
from core.external_conversation import ExternalConversationController


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

    @property
    def exit_keywords(self):
        return list(dict.fromkeys(super().exit_keywords + ["拜拜","再见","没事了","不用了"]))

    async def start(self):
        try:
            await super().start()
        finally:
            if self._turn_tools:
                await self._turn_tools.finish_feedback(False)

    async def _request_response(self, text):
        from core.ref import get_app
        from core.services.conversation_tools import ConversationTools
        app = get_app()
        if self._cfg("tools_enabled", False):
            if app is None:
                raise RuntimeError("受控工具需要 MainApp 审计服务")
            self._turn_tools = ConversationTools(app.native_bridge, self.backend._session_key, home=app.home_assistant, user_text=getattr(self,"_current_user_text",text))
        else:
            self._turn_tools = None
        response, self._turn_voice = await self.backend.chat_turn(text, self._turn_tools)
        if not response and self._turn_tools:
            await self._turn_tools.finish_feedback(False)
        return response

    async def _play_tts(self, text):
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
            return await self._turn_tools.finish_feedback(self._feedback_succeeded)
        return False
