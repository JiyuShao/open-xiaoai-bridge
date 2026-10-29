# 03: xiaoai-bridge 原生指令与操作记录闭环

Status: ready-for-agent
Execution: completed-mock; real-hermes-tools-and-device-pending; silent-tests-only

**Parent:** [小爱豆包 spec](../spec.md)

**What to build:** 小爱豆包通过本项目的 `xiaoai-bridge` skill 提交原生指令，获得准确的受理、拒绝或未知结果，并查询持久化操作记录。统一迁移原有播放控制入口，测试中由假音箱执行协议应答。

**Blocked by:** 01 小爱豆包文字对话与默认会话。

依据：小爱豆包 spec、第二份 ADR。优先复用已有 `ask_xiaoai` 和播放原语；不直接接 HA，不向 Agent 开放 shell。所有实际设备调用均替换为测试传输，生产音箱与生产工具不可达。

- [x] 本项目创建 `xiaoai-bridge` skill，提供原生命令及已有必要播放、状态、打断和唤醒能力，迁移现有 `xiaoai-tts` 入口及引用。
- [x] 经受控接口提交自然语言指令，工具调用不会把任意 shell 能力暴露给模型。
- [x] 文本含单引号、换行及 shell 特殊字符时能保持原意传输，不形成命令注入。
- [x] 豆包原生指令以静默反馈目标提交，返回准确区分受理、拒绝、超时及未知；无业务证据时不能报告完成。
- [x] 静默原生参数在 Mock 中可验证；真实抑制效果和原生结果采集能力明确保留为设备待验项。
- [x] 操作以请求标识关联；重复投递不重复执行，超时或重启后未知状态不自动重放，新的有意操作不被误去重。
- [x] 操作前后状态、实际返回及错误持久化保存，重启后可查询；业务完成证据与模拟结果明确区分。
- [x] 记录时间、session、指令、参数、受理情况、返回、错误和确认程度；凭据不进入记录，不额外保存聊天录音。
- [x] 测试工具仅指向假音箱 API，真实 Hermes 验证也必须使用专用 profile 和受限工具集合。
- [x] 测试覆盖成功应答、拒绝、超时、重复、异常中断和恢复后的下一次有意操作。

## Comments

2026-10-29：完成 `OperationRecords`、`NativeBridgeService`、受控原生指令/播报/状态/打断 API 和 `xiaoai-bridge` skill；各动作先持久化，再调用设备，相同 ID 不重发，不同参数冲突会拒绝。正式动作名为四个 `xiaoai_*`；唤醒只保留为 Bridge 调度 API，任意文件/URL 播放不进入 Agent 工具表。旧 `xiaoai-tts` 入口已迁移，README 与默认 skill 提示词同步更新。

原生 `ask_xiaoai_result` 保留 `stdout/stderr/exit_code`，完整 JSON 用 `shlex.quote` 封装。只有可解析的接口 code 能判定受理/拒绝；传输失败、超时、取消和重启中断均为 `unknown`，不自动重试，也不声称业务完成。SQLite 保留脱敏后的返回、参数、session、时间和确认程度，新增 Compose 持久化卷但没有启动/部署。记录删除须用户明确要求，删除会同时移除对应去重保护。

验证使用 FakeSpeaker 及 HTTP/CLI→真实 SpeakerManager→假设备 RPC，TTS 流式与非流式均替换为不发声的接收器。记录明确标为 `evidence=mock`；真实 Hermes profile/工具权限、音箱静默抑制与原生业务结果采集仍待后续隔离验证和真机授权。本次不连接音箱、不调用真实模型或 TTS、不部署、不 push。

最终检查：临时 Python 3.12 测试环境完整回归 124 项（121 项通过、3 项 live 测试跳过），其中任务 03 定向测试 24 项；mypy、compileall 与 `git diff --check` 通过。双轴复核发现的注入、原始返回丢失、缓存状态误标、迟到播报和文本凭据脱敏问题均已修复并验证；两轴最终静态复核无阻断发现。子代理复跑受回环 socket 沙箱权限限制，运行通过证据来自主任务获准执行的静默测试。系统 Python 缺依赖的首次检查不作为代码失败或通过证据。
