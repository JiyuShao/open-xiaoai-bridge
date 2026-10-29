# 09: 原生小爱与 Home Assistant 路由及控制回执

**What to build:** 用户在小爱豆包中请求控制设备时，系统按已确认规则选择原生小爱或 Home Assistant，并返回可核实的结果。原生小爱优先覆盖米家和已知原生能力，Home Assistant 补充非米家或原生不支持的设备；系统不对同一请求双发。

**Blocked by:** 04: 原生小爱与小爱豆包的混合唤醒路由；08: Home Assistant 连接与语义工具边界

Status: ready-for-agent
Execution: completed-mock (2026-10-29); real-services-and-deployment-pending

- [x] 明确要求 Home Assistant 时强制走 Home Assistant；已知米家或原生能力时优先走 `xiaoai_command`；已知非原生设备时走 Home Assistant。
- [x] 无法判断目标路径时先询问用户，不猜测、不同时执行两条路径。
- [x] Home Assistant 控制在调用后查询状态或取得明确失败结果；原生小爱区分 `accepted`、`completed` 和 `unknown`。
- [x] 没有业务完成证据时只播报已提交或结果未知，不声称设备已经完成动作。
- [x] 门锁、燃气、热水器和取暖器等高风险操作拒绝自动执行或要求明确确认；普通低风险明确请求可直接执行。
- [x] 路由、调用参数、结果和确认状态进入持久化操作记录，重复请求不会因重试而双发。

## Comments

2026-10-29：按用户“暂时全部使用 Mock”执行。软件实现与协议替身验收完成；勾选项表示 Mock 证据，不表示生产服务或真实设备已完成。实际 Hermes profile、HA 配置/触发、Recorder365部署与真实 TTS 均待专用环境验证。实现与限制见 docs/home-assistant-tools.md；静默验收见 docs/doubao-silent-validation.md。
