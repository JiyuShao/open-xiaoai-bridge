# 10: 设备历史查询与 365 天 Recorder

**What to build:** 用户可以在小爱豆包中查询设备过去的状态和使用情况，并得到可直接播报的统计结果。Home Assistant Recorder 目标保留 365 天，同时只记录家庭控制、告警和历史查询需要的实体，避免无关高频实体拖大数据库。

**Blocked by:** 03: xiaoai-bridge 原生指令与操作记录闭环；08: Home Assistant 连接与语义工具边界

Status: ready-for-agent
Execution: completed-mock (2026-10-29); real-services-and-deployment-pending

- [x] 支持今天、昨天、过去 24 小时、过去 7 天、本月和过去 30 天等时间范围，并统一使用 Asia/Shanghai。
- [x] 开关、灯、风扇等设备能返回开启总时长、次数、开始/结束时间和可用的触发来源。
- [x] 温度、湿度、功率和电量实体能返回适合语音播报的最小值、最大值、平均值或累计值；没有记录时明确返回无记录。
- [x] 统计由服务端代码完成，模型只负责理解问题和播报结果，不自行加总时间或猜测功率。
- [ ] Home Assistant Recorder 配置为 365 天保留，保留自动 purge/repack，并设置实体 include/exclude 过滤。
- [x] 记录数据库大小、查询延迟和可用磁盘基线；查询失败、历史不足或离线时如实标记。

## Comments

2026-10-29：按用户“暂时全部使用 Mock”执行。软件实现与协议替身验收完成；勾选项表示 Mock 证据，不表示生产服务或真实设备已完成。实际 Hermes profile、HA 配置/触发、Recorder365部署与真实 TTS 均待专用环境验证。实现与限制见 docs/home-assistant-tools.md；静默验收见 docs/doubao-silent-validation.md。

服务器隔离Mock基线已落盘：5764行/348160字节，查询6.42–7.35ms，测试目录可用盘8237137920字节。以上不代表生产HA基线，未应用Recorder配置。
