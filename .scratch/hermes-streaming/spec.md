# Hermes 流式对话性能

## Problem Statement

小爱豆包当前等待 Hermes 返回完整回复后才开始处理文字和 TTS。每轮还会重新建立 HTTP 客户端，导致首句等待时间偏长；长回复必须完整生成后才有机会播报。流式改造还需要处理工具进度、分句边界、播放顺序、打断和旧回复清理，否则会造成重复请求、乱序播报、把未完成的设备操作误报为成功，或让迟到的旧回复污染新一轮对话。

## Solution

Bridge 使用 Hermes 已有的 OpenAI-compatible Chat Completions 流式接口，以一次用户轮次一个请求、同一个 session 为边界。Bridge 复用 HTTP 连接，发送 `stream=true`，解析标准 SSE `data: choices[].delta`，同时识别可选的 `event: hermes.tool.progress`、完成事件和 `[DONE]`。内部统一成文本增量、工具进度、轮次完成和流错误事件。

普通聊天收到完整句子后立即进入有序 TTS 队列，句子按生成顺序播放；分句器保留未完成片段，流结束时刷新剩余文本。工具进度事件只更新内部状态，不直接播报；Hermes 没有自然语言进度话术时 Bridge 保持安静，不自行编造“正在处理”等语义。若提供可选的 Hermes 中性前置提示，提示必须贴切，并遵循当前 session 的语种、方言和情绪。

工具请求以真实工具结果和最终 Hermes 回复为准。Bridge 不在工具完成前播报“已打开”或“已完成”，不重发有副作用的操作。打断时停止当前音频、取消旧流、清空未播放队列并丢弃迟到事件；已经提交的设备操作不回滚、不重发。流式能力不可用、SSE 格式不兼容或中途失败时回退到完整响应路径。

Hermes 不修改代码、不修改镜像、不改变人格、记忆、语种、方言、情绪或工具选择职责。Bridge 只负责协议解析、事件转换、分句、队列、取消、顺序和播放。所有验收使用 Mock Hermes、SSE 夹具、文件输出或静默 TTS 接收器；不连接生产 HA、不写 HA、不重启容器、不让音箱发声。

## User Stories

1. As a 小爱豆包用户, I want the first complete sentence to begin播报 before the whole answer is generated, so that short replies feel faster.
2. As a 小爱豆包用户, I want a long answer to play sentence by sentence in order, so that I do not wait for the entire response.
3. As a 小爱豆包用户, I want the reply to keep Hermes 的人格和自然语言, so that streaming does not change the assistant’s voice.
4. As a 小爱豆包用户, I want the current session 的语种、方言和情绪 to apply to each streamed sentence, so that sentence-level TTS does not reset preferences.
5. As a 小爱豆包用户, I want punctuation and explicit boundaries to produce natural sentence chunks, so that TTS does not speak broken fragments.
6. As a 小爱豆包用户, I want a final partial sentence to be spoken once when the stream ends, so that no generated text is lost.
7. As a 小爱豆包用户, I want Hermes’s natural-language progress sentence to be spoken when present, so that I understand a long-running operation.
8. As a 小爱豆包用户, I want raw tool progress events to stay silent, so that protocol metadata is never read aloud.
9. As a 小爱豆包用户, I want no invented progress message when Hermes provides none, so that Bridge does not claim work that Hermes did not say.
10. As a 小爱豆包用户, I want a neutral optional preamble to use my current language, dialect and emotion, so that the preamble sounds consistent.
11. As a 小爱豆包 user, I want a tool-driven reply to speak only after the real tool result is available, so that the assistant never announces an unverified device action.
12. As a 小爱豆包 user, I want one user turn to create one Hermes request and one session, so that streaming cannot duplicate a command.
13. As a 小爱豆包 user, I want to interrupt a reply and immediately start a new turn, so that old audio does not continue talking over me.
14. As a 小爱豆包 user, I want late chunks from an interrupted reply discarded, so that an old answer cannot appear in the new conversation.
15. As a 小爱豆包 user, I want an already submitted device action recorded without rollback or retry, so that interruption does not create a second side effect.
16. As a maintainer, I want a reusable HTTP connection, so that repeated turns avoid connection setup overhead.
17. As a maintainer, I want standard SSE parsing with explicit event types, so that malformed or unknown frames fail safely.
18. As a maintainer, I want stream failures to fall back to the existing complete-response path, so that Hermes installations without streaming remain usable.
19. As a maintainer, I want stage timing for request, first byte, first text, first sentence, TTS start, tool completion and final response, so that latency regressions can be diagnosed.
20. As a maintainer, I want queue cancellation and ownership to be explicit, so that one conversation cannot stop another conversation’s playback.
21. As a maintainer, I want silent tests to capture output bytes instead of playing audio, so that automated validation is safe around the family.
22. As a maintainer, I want capability detection cached per Hermes endpoint, so that streaming is attempted only when supported and does not add repeated probes.
23. As a maintainer, I want the non-streaming fallback to preserve the existing tool and voice contracts, so that compatibility changes are isolated.
24. As a maintainer, I want tests for empty deltas, finish markers, comments, malformed JSON, disconnects and duplicate terminal frames, so that protocol edge cases are deterministic.

## Implementation Decisions

- Add a protocol seam that converts SSE frames into `TextDelta`, `ToolProgress`, `TurnFinished` and `StreamError` events. Unknown events are ignored or surfaced as protocol diagnostics without being spoken.
- Send `stream=true` only for the streaming capability path. The payload keeps one user message, one session key and the existing Hermes-native headers; the Bridge never synthesizes a second request for the same turn.
- Reuse one `aiohttp` client session per OpenAI manager lifecycle, with a bounded connector and explicit close on shutdown. Redirects remain disabled.
- Add a sentence aggregator that emits only complete, non-empty chunks and flushes its remainder at turn completion. It must preserve order and never emit the same text twice.
- Add an owned streaming playback queue. Each turn owns its queue, cancellation token and TTS tasks. Cancellation stops only that owner’s audio, clears pending chunks and rejects late events by generation/token.
- Route each emitted sentence through the existing TTS router with the current `VoiceDirective`; no provider-specific language or dialect inference is added to the Bridge.
- Keep tool progress events out of the spoken queue. Only Hermes text deltas can become spoken content, and tool-related text is spoken only when Hermes emits it.
- Preserve the existing complete-response implementation as the fallback for capability absence, non-SSE responses, malformed streams and transport errors before any irreversible side effect is submitted.
- Instrument monotonic stage timings in structured in-memory/log-safe diagnostics. Do not include credentials, full user text, raw audio or HA payloads in timing records.
- Treat a finish marker, `[DONE]` and connection close after a valid final event as idempotent terminal signals. A stream that ends without a terminal event is a `StreamError` and may fall back when no irreversible tool request has been submitted.
- Keep VAD, AEC, playback-time custom wake interruption, Hermes code/image, HA production access and device operations out of this feature.

## Testing Decisions

- Test the highest seam that accepts an HTTP response stream and produces observable text/audio events. Use an in-memory SSE response fixture and a silent TTS receiver.
- Test the public conversation boundary for one request per user turn, same session headers, stream fallback, cancellation, ordered queue output and voice directive propagation.
- Test the TTS boundary with a fake provider that records sentence order and cancellation, never the speaker or Rust playback device.
- Test timing fields as monotonic presence/order and bounded values, not exact wall-clock durations.
- Test tool progress with protocol fixtures and assert it is never enqueued as speech; use fake tool results for completion/error cases.
- Test cancellation with delayed frames after cancellation and assert no late text reaches the TTS receiver.
- Test non-streaming fallback with a complete JSON response and assert the existing response contract remains unchanged.
- Run focused unit tests per ticket, then the native wake and full isolated test suites. Production HA, Hermes, Docker and audio hardware are explicitly excluded.

## Out of Scope

- Modifying Hermes source, image, profile implementation, memory store or tool executor.
- VAD latency tuning, AEC, playback-time custom wake interruption, microphone echo suppression or firmware changes.
- ASR model tuning, server CPU/GPU tuning, model replacement or speculative parallel generation.
- Concurrent unordered playback, automatic retries for side-effecting tools, rollback of submitted operations or invented progress wording.
- Real Home Assistant reads/writes, real Hermes network probes against production, real TTS credentials, speaker playback or device/container restarts.

## Further Notes

- The language and progress boundary is recorded in ADR-0008 and the terms are defined in the project glossary.
- A capability probe may use a local Mock Hermes endpoint only. It must not be interpreted as evidence that the production endpoint supports streaming until a separate authorized read-only check exists.
- The first measurable target is lower first-sentence latency while preserving current final reply correctness and cancellation safety; exact latency targets are to be recorded from silent fixtures rather than guessed from wall-clock tests.
