# 13: 连续对话中的 Home Assistant 工作流与退出词

**What to build:** 用户唤醒“豆包豆包”后，可以在同一段连续对话中控制 Home Assistant、查询设备历史、创建规则和安排定时任务；短操作反馈后继续等待下一轮讲话，说出退出词即可立即结束会话。

**Blocked by:** 05: 原生操作由小爱豆包反馈并正确让出播放；06: 文件语音进入小爱豆包并验证现有 ASR；09: 原生小爱与 Home Assistant 路由及控制回执；10: 设备历史查询与 365 天 Recorder；11: 语音创建场景和自动化；12: 定时任务与提醒

Status: ready-for-agent
Execution: completed-mock (2026-10-29); real-services-and-deployment-pending

- [x] 连续对话中可完成低风险 HA 控制、历史查询、规则创建和定时任务创建，并保持同一 session 上下文。
- [x] 说“拜拜”“再见”“没事了”“不用了”等退出词后立即结束连续对话，不再等待下一轮讲话。
- [x] 每轮 VAD/ASR 状态独立重置，空识别、未听清和超时不会复用上一轮内容或触发猜测性操作。
- [x] 短操作反馈后继续监听；持续播放或切回原生小爱时，旧会话不会重复执行操作或覆盖新会话输出。
- [x] 通过文件音频和假音箱验证多轮停顿、退出词、HA 工具调用顺序、确认流程和失败恢复，不播放真实音频。

## Comments

2026-10-29：按用户“暂时全部使用 Mock”执行。软件实现与协议替身验收完成；勾选项表示 Mock 证据，不表示生产服务或真实设备已完成。实际 Hermes profile、HA 配置/触发、Recorder365部署与真实 TTS 均待专用环境验证。实现与限制见 docs/home-assistant-tools.md；静默验收见 docs/doubao-silent-validation.md。
