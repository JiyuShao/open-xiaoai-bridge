# 12: 定时任务与提醒

**What to build:** 用户可以通过语音创建一次性或周期性的设备定时任务，也可以创建只播报消息的提醒。任务创建、查询、修改和删除均通过受限工具完成，提醒统一交给 `xiaoai_announce` 播报。

**Blocked by:** 03: xiaoai-bridge 原生指令与操作记录闭环；08: Home Assistant 连接与语义工具边界

Status: ready-for-agent
Execution: completed-mock (2026-10-29); real-services-and-deployment-pending

- [x] 支持“几小时后”“今天某时”“每天某时”等一次性和周期性时间表达，并向用户复述实际执行时间。
- [x] 设备定时任务调用 Home Assistant；普通提醒调用 `xiaoai_announce`；每日设备汇报可先查询设备历史再播报。
- [x] 创建前向用户展示目标、动作、时间、重复方式和播报内容，用户确认后才保存后台任务。
- [x] 查询、修改和删除任务先按任务 ID 或明确匹配结果操作，不猜测任务编号；修改关键字段时重新确认，删除需明确确认。
- [x] 后台任务只拥有完成工作所需的受限工具集合，不能访问 shell、文件、浏览器或 Docker。
- [x] 到点执行失败记录为 `missed` 或 `failed`，不自动补执行设备动作；提醒失败可在服务恢复后报告。

## Comments

2026-10-29：按用户“暂时全部使用 Mock”执行。软件实现与协议替身验收完成；勾选项表示 Mock 证据，不表示生产服务或真实设备已完成。实际 Hermes profile、HA 配置/触发、Recorder365部署与真实 TTS 均待专用环境验证。实现与限制见 docs/home-assistant-tools.md；静默验收见 docs/doubao-silent-validation.md。
