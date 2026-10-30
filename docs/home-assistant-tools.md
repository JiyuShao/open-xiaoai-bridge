# 小爱豆包的 Home Assistant 工具

写操作验收继续使用 Mock；真实 Hermes 和 Home Assistant 只读连接可以单独启用。`profiles/xiaoai-doubao.json` 与 `profiles/xiaoai-doubao-readonly.json` 是本地权限清单和测试合同，不能通过 OpenAI-compatible 请求切换 Hermes 的服务端活动 profile。Hermes 当前把活动 profile 作为服务端全局配置；Bridge 通过真实 `/v1/chat/completions` 接入，并在本地按对应合同过滤工具。部署前需在 Hermes 服务端选择与 Bridge 配置一致的 profile。模型只能使用请求中提供的语义工具；未知名称、任意参数、管理接口和重定向均被拒绝。

## 配置与边界

私有 `home_assistant` 配置包含 `enabled`、不带凭据/路径的 `base_url`、运行时 `token`、`entities`、可选 `read_only` 和 `workflows_path`（默认 `app/home-workflows.db`）。实体声明真实 `name`、`native: true|false`；未知原生映射不猜测。传感器可声明 `unit`、电量累计可声明 `history_kind: energy`，非标准状态/事件需明确列出 `states`/`events`。凭据不进入提示词、记录或普通日志。

`read_only: true` 时只向 Hermes 暴露 `ha_entities`、`ha_state`、`ha_history`、`ha_services`；不启动定时任务和自动化事件监听，也拒绝 `device_control`、规则、任务及任何写入。真实服务只读检查使用：

```sh
HERMES_BASE_URL=http://.../v1 HA_BASE_URL=http://... \
  HERMES_API_KEY=... HA_TOKEN=... \
  python -m scripts.verify_live_readonly
```

可选 `HA_ENTITY_IDS=light.living,sensor.temperature` 检查指定状态，`HA_HISTORY_ENTITY=sensor.temperature` 检查该实体过去24小时历史；未提供历史实体时不对历史作结论。`--chat-text` 只向真实 Hermes 发送无工具连通性请求；脚本不调用 HA 写接口、Bridge 或音频播放。结果只打印状态码、实体状态和数量，不打印凭据。`X-Hermes-Session-Key` 只隔离服务端记忆作用域，不能当作 profile 权限隔离证明。

启动时先验证配置再启动设备监听。定时任务和自动化提醒订阅由 `MainApp.loop` 托管，退出时取消；运行库应由单一 Bridge 进程独占，不能将同一 SQLite 文件提供给多个运行实例。启用这些功能会增加到点播报路径；当前只运行隔离 Mock，没有将其部署到生产。

`ha_entities`、`ha_state`、`ha_services` 和 `ha_history` 只读允许实体。`device_control` 依据当前用户原话和配置选择唯一原生/HA路径，模型的 route 字段不构成授权。明确要求 HA 时强制 HA，否则已知原生优先，未知路径返回 needs_info。HA 调用后读取状态，匹配才返回 completed/observed_state；未观察到目标或传输异常为 unknown，不自动重试。原生仅报告 accepted_only。选路、实体、动作、确认依据和结果进入共享 SQLite 审计，一轮不能换路径双发。

门锁、climate、燃气、热水器、取暖器及 `risk: high` 强制确认，且不得加入后台规则或定时动作。确认绑定 session、实体、动作和选路，并要求下一轮用户原话为“确认/同意/是的/确认执行”；5 分钟过期、一次消费。设备确认仅在内存，重启需重新询问。启用 HA 时，自由文本原生工具只接受精确的持续播放音乐请求；设备操作必须走实体绑定的工具，以免绕过选路和确认。

## 历史

`ha_history` 支持今天、昨天、过去24小时、过去7天、本月、过去30天，使用 Asia/Shanghai。服务端裁剪窗口、统计 on 状态时长和窗口内开启次数，保留区间及可用 context 来源。温度等数值采用时长加权平均，并返回最小/最大值；电量按正增量与计数器重置累计。无记录明确返回 no_records，unknown/unavailable 和缺失前段报告 partial/missing_seconds，不猜测来源或功率。

运行 `python -m scripts.recorder_plan --entities <允许实体JSON> --output <独立目录>` 生成 Recorder 365 天、auto_purge、auto_repack 与 include/exclude 配置，以及 Mock 数据库大小、端到端查询耗时、可用磁盘基线。生成的 recorder.yaml 为合法 YAML 的 JSON 表示，须人工合并到 HA；命令不应用配置。Mock 基线不能推算真实 HA 容量、增长或性能，实际保留期与历史缺口尚待专用环境验证。

## 场景、自动化和定时

`ha_rule_propose` 接受 scene 或 automation：场景绑定 phrase 与实体动作；自动化支持状态、数值上阈值、每日时间或配置允许的事件触发，可加状态/数值条件。提案和确认前都读取真实协议返回的实体/服务并验证状态，禁止任意模板、脚本、服务参数和管理调用。保存前复述设备、动作、条件和影响范围。使用 `ha_pending` 查询当前 session 的待确认引用，然后 `ha_rule_confirm` 确认；拒绝或过期不保存。修改/删除同样重新确认，规则只能在本系统 `xiaoai_bridge_` 命名空间与本地所有权记录内管理。

场景通过 `ha_scene_run` 执行，逐个读取设备结果，未全部验证时停止并报告未知。自动化写入 HA 的配置接口并回读验证保存；这表示配置保存成功，不表示触发时的设备动作已经完成。实际 HA 配置 API 兼容性、配置加载及真实触发效果尚待验收。

自动化允许 `{"announce":"提醒文字"}` 动作，只发布 `xiaoai_bridge_announce` 事件及规则ID/版本/动作序号。Bridge 只订阅这一事件，检查本地已确认规则和 HA 回读配置，从本地取播报文字，忽略事件携带的任意文本；以 context、规则、版本和动作序号去重，旧版本或不匹配事件拒绝，重连不补播。

`ha_task_propose` 支持 device、reminder、history_report，时间为 N小时后/N分钟后、今天HH:MM、每天HH:MM，复述精确上海时间后通过 `ha_task_confirm` 保存。查询、修改和删除使用明确ID；关键变更重新确认。提醒统一记录为 `xiaoai_announce` 并走小爱豆包 TTS；历史汇报先计算统计。设备任务只有受限 HA 动作，无法访问 shell/文件/浏览器/Docker。晚于计划超过60秒记 missed，失败记 failed，不自动追赶；重启时在途实例失败，每日任务保留下一次未来计划。

每个 session 只保留最新待确认提案；新提案会取代旧引用，避免模型用模糊确认执行旧请求。规则/任务提案保存在私有 SQLite，确认绑定 session、类型与对象版本，先消费确认再执行，跨进程结果未知不重放。模型仅获 `ha_pending` 当前会话引用，不必把内部ID读给用户。声音偏好与这些持久规则分离，仍仅影响当前 session 阶段。

## 验收限制

文件音频和 Mock 写操作已覆盖控制、历史、场景、跨轮确认、提醒与退出。真实只读 Hermes/HA 检查通过 `scripts.verify_live_readonly` 进行；真实 Hermes 服务端 profile 是否已按合同配置、HA 写接口/触发结果、豆包音色、音箱和远场仍未验收。所有音频进入测试接收器或文件，Mock 写操作的完成只代表替身状态已观测；没有应用 Recorder、变更生产配置或连接音箱。
