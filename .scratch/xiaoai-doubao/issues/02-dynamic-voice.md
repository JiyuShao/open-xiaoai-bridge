# 02: Vivi 自动播报与模型判断的阶段声音偏好

Status: ready-for-agent
Execution: completed-mock; real-hermes-tts-pending; silent-tests-only

**Parent:** [小爱豆包 spec](../spec.md)

**What to build:** 小爱豆包每轮自动输出回复及合适的声音要求，在静默环境生成测试音频或记录合成请求。Hermes 根据 session 上下文动态决定情绪、语种、方言与阶段偏好，不要求每轮调用播放工具。

**Blocked by:** 01 小爱豆包文字对话与默认会话。

依据：小爱豆包 spec、第二份 ADR。默认声线固定为 Vivi 2.0（`zh_female_vv_uranus_bigtts`）；语气默认亲切自然，用户明确要求优先。只做当前 session 的阶段偏好，不建立长期声音画像。所有音频只保存或交给测试接收器，不能播放，降级路径亦如此。

- [x] 明确并验证现有 Hermes 通道返回回复文字及本轮声音要求的方式：兼容 `message.voice`、`message.metadata.voice` 和顶层 `voice`，不强制真实 Hermes 已支持该 schema。
- [x] 普通回复沿用共享 TTS 路由，无需 Agent 每轮调用播放 skill，声音元数据不会进入朗读文本。
- [x] 流式、非流式和 Python/Rust 路径传递声音要求；Rust 请求不再把语种固定为中文，默认仍为 `zh`。
- [x] 按 TTS 2.0 协议传递 `context_texts`、语种和明确方言；Vivi 继续使用自然语言语气指令，情绪字段保持可选。
- [x] 支持当前进程 session 阶段偏好；单句 directive 默认不持久化，`session_preference=true` 才延续，新会话清除。
- [x] 参数类型、长度、方言白名单及异常模型输出得到校验或安全回退，不重发业务操作。
- [x] TTS 凭据仍只从私有配置读取；凭据缺失时文字 Mock 可运行且不回显秘密。
- [ ] 覆盖普通话、英语、四川话输出与情绪控制请求；当前完成请求体和 Mock 参数测试，真实合成到文件仍待凭据。
- [ ] Vivi 不可用时通过同一测试接收器验证原生音色降级与原因记录，不调用真实原生 TTS。
- [ ] 模型判断效果、真实合成、音色听感分别列明证据及待测项。

## Comments

2026-10-29：新增 `VoiceDirective` 校验边界，支持单句语音要求和当前 session 阶段偏好；OpenAI 兼容回复只读取可选元数据，不把它拼入朗读文本。统一 TTS Router、豆包 Python payload、Rust 流式/非流式/静默收集接口已透传 `context_text`、`explicit_language`、`explicit_dialect`、`emotion`。新增 `tts_output_mode="record"`，静默测试只记录合成请求，不访问凭据、网络、音箱或降级播放；记录失败也不会回退到原生播报；Vivi 作为缺省音色。定向测试 22 项通过，完整离线回归 100 项（3 项真实服务测试跳过）；真实 Hermes schema、豆包凭据合成、试听和音箱播放尚未验证。
