# 11: 语音创建场景和自动化

**What to build:** 用户通过自然语言教会小爱豆包一个场景或自动化。系统先根据 Home Assistant 的真实实体和服务检查规则，再用设备真实名称复述，用户确认后保存；场景由用户口令触发，自动化由设备状态、事件、数值或时间触发。

**Blocked by:** 03: xiaoai-bridge 原生指令与操作记录闭环；08: Home Assistant 连接与语义工具边界

Status: ready-for-agent
Execution: completed-mock (2026-10-29); real-services-and-deployment-pending

- [x] “以后我说某句话就执行动作”可以创建场景；“设备发生某事后执行动作或提醒”可以创建自动化。
- [x] 保存前检查实体、服务、状态值、条件和动作参数，发现不存在或不安全的内容时不保存并说明原因。
- [x] 保存前复述目标设备、动作、触发条件和影响范围；只有用户明确同意后才写入 Home Assistant。
- [x] 场景和自动化使用本系统可识别的命名空间，只能查看、修改和删除本系统创建的规则。
- [x] 修改目标、动作、条件或触发方式时重新确认；删除前明确确认；用户拒绝确认时不产生变更。
- [x] 触发场景或自动化后只有取得真实执行结果才播报完成，设备未响应时明确报告未完成。

## Comments

2026-10-29：按用户“暂时全部使用 Mock”执行。软件实现与协议替身验收完成；勾选项表示 Mock 证据，不表示生产服务或真实设备已完成。实际 Hermes profile、HA 配置/触发、Recorder365部署与真实 TTS 均待专用环境验证。实现与限制见 docs/home-assistant-tools.md；静默验收见 docs/doubao-silent-validation.md。
