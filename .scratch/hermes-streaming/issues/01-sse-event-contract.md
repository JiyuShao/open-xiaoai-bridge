# 01: Hermes SSE 事件合同

**What to build:** Bridge 能把 Hermes 的标准 SSE 文本增量、工具进度、完成和错误帧转换成稳定的内部流式事件，让后续对话管线不需要理解传输格式。

**Blocked by:** None (can start immediately)

**Status:** ready-for-human

- [x] 标准 `data: choices[].delta` 文本帧转换为 `TextDelta`，保留顺序和空增量语义
- [x] 识别 `event: hermes.tool.progress`、完成事件和 `[DONE]`
- [x] 处理 SSE 注释、跨块行、空行、非 JSON、重复终止和连接提前关闭
- [x] 工具进度不进入语音输出，协议错误形成可区分的 `StreamError`
- [x] Mock SSE 夹具覆盖正常、空增量、畸形帧、断连和重复终止
