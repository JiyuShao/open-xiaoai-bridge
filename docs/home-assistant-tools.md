# 小爱豆包的 Home Assistant 工具

生产 Bridge 的 HA 模式由 `HA_READ_ONLY` 和 `HA_ALLOW_ALL` 共同决定。`HA_ALLOW_ALL=1` 且 `HA_READ_ONLY=0` 时，Bridge 向 Hermes 暴露任意实体读取、服务列表和 `ha_call_service`：`domain`、`service` 与 `data` 原样交给 Home Assistant，服务调用结果进入操作记录并按请求 ID 去重。该模式放开的是 Home Assistant API 范围，不授予服务器 shell、Docker、文件系统、音箱运维或 Hermes 管理权限。规则和定时任务仍使用本系统提案/确认/持久化流程，调用的 HA 服务不再受实体白名单限制。

`HA_ALLOW_ALL=0` 时沿用实体元数据白名单和语义控制边界；`HA_READ_ONLY=1` 时只读。`profiles/xiaoai-doubao.json` 与 `profiles/xiaoai-doubao-readonly.json` 是本地 profile 合同，不能通过 OpenAI-compatible 请求切换 Hermes 的服务端活动 profile。Hermes 当前把活动 profile 作为服务端全局配置；Bridge 通过真实 `/v1/chat/completions` 接入，并在本地按当前运行模式过滤工具。Hermes 自身的服务端工具仍由 Hermes 管理，不受请求里的 `tools: []` 影响。

## 配置与边界

项目根目录的 [`.env.services.example`](../.env.services.example) 是唯一运行变量模板，统一管理启动模式、音箱 Token、Hermes、HA 与豆包 TTS（凭据、音色、语速）；`.env.example` 只保留指向它的说明。服务器统一在部署目录 `.env.services` 填写，设置 `chmod 600 .env.services`；私有文件已被 Git 忽略。Python 配置只读取注入变量，无需在代码中重复填写。

配置文件由程序按运行模式自动选择，日常变量文件不填写 `CONFIG_PATH`；该变量仅保留给自定义配置的高级覆盖场景。

在服务器 Compose 的 Bridge 服务中引用（相对于 Compose 文件目录）：

```yaml
services:
  bridge:
    env_file:
      - ./.env.services
```

`env_file` 将运行变量注入容器；Compose 不再重复填写这些变量的值。`--env-file` 为 YAML 中的 `${...}` 替换提供值；模型挂载等 Compose 参数仍在编排中配置。已有 `environment` 中的同名变量优先于 `env_file`，不要在两处重复定义。原生/混合隔离测试编排继续使用显式测试参数，不能注入生产密钥。现有混合模式示例使用隔离网络，接真实服务还需配置可访问服务的网络。HA 只读开关不会关闭音频；静默验收运行独立的 `scripts.verify_live_readonly`，不启动音箱主程序。更改私有文件后需重建容器才会加载；生产切换记录和回退备份见 `docs/automated-acceptance.md`。

私有 `home_assistant` 配置包含 `enabled`、不带凭据/路径的 `base_url`、运行时 `token`、可选 `entities`、`read_only`、`allow_all` 和 `workflows_path`（默认 `app/home-workflows.db`）。`allow_all` 由统一环境变量 `HA_ALLOW_ALL=1` 注入；此时 `entities` 可为空，但仍可放置名称、状态或事件元数据供规则/历史播报使用。白名单模式下实体声明真实 `name`、`native: true|false`；未知原生映射不猜测。传感器可声明 `unit`、电量累计可声明 `history_kind: energy`，非标准状态/事件需明确列出 `states`/`events`。凭据不进入提示词、操作记录或普通日志；任意服务调用的审计参数只保存数据键名和 SHA-256 摘要。

`read_only: true` 时只向 Hermes 暴露 `ha_entities`、`ha_state`、`ha_history`、`ha_services`；不启动定时任务和自动化事件监听，也拒绝 `device_control`、规则、任务及任何写入。真实服务只读检查使用：

```sh
HERMES_BASE_URL=http://.../v1 HA_BASE_URL=http://... \
  HERMES_API_KEY=... HA_TOKEN=... \
  python -m scripts.verify_live_readonly
```

可选 `HA_ENTITY_IDS=light.living,sensor.temperature` 检查指定状态，`HA_HISTORY_ENTITY=sensor.temperature` 检查该实体过去24小时历史；未提供历史实体时不对历史作结论。`--chat-text` 已禁用，传入时在任何网络请求前拒绝：当前 Hermes 不能按请求禁用服务端工具。脚本仅做 GET 读取，不调用 HA 写接口、Bridge 或音频播放。结果只打印状态码、实体状态和数量，不打印凭据。`X-Hermes-Session-Key` 只隔离服务端记忆作用域，不能当作 profile 权限隔离证明。

启动时先验证配置再启动设备监听。定时任务和自动化提醒订阅由 `MainApp.loop` 托管，退出时取消；运行库应由单一 Bridge 进程独占，不能将同一 SQLite 文件提供给多个运行实例。启用这些功能会增加到点播报路径；当前只运行隔离 Mock，没有将其部署到生产。

`ha_entities`、`ha_state`、`ha_services` 和 `ha_history` 在白名单模式只返回允许实体，在 allow-all 模式返回 Home Assistant 返回的实体/服务范围。allow-all 的 `ha_call_service` 直接执行任意已注册服务；HTTP 成功记录为 `completed/ha_http_200`，传输或超时记录为 `unknown`，不会自动重试。白名单模式的 `device_control` 仍依据当前用户原话和配置选择唯一原生/HA路径，模型的 route 字段不构成授权。明确要求 HA 时强制 HA，否则已知原生优先，未知路径返回 needs_info。选路、实体、动作、服务摘要、确认依据和结果进入共享 SQLite 审计，一轮不能换路径双发。

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

文件音频和 Mock 写操作已覆盖控制、历史、场景、跨轮确认、提醒与退出。真实只读 Hermes/HA 检查通过 `scripts.verify_live_readonly` 进行；卧室台灯是唯一已做真实写入回读的设备，其他 HA 写接口、规则和提醒仍未逐项验收。所有隔离音频进入测试接收器或文件，Mock 写操作的完成只代表替身状态已观测。


## 2026-10-31 软件与真实只读验证

本节早期真实 Hermes 样本使用后来已移除的文字代理，不能代替原生直连验收。现行方案见[Hermes 原生直连](ambient-recording.md#hermes-原生直连)；Hermes 自行执行 skill，通过 Bridge 的鉴权接口使用 HA 工具。隔离测试中实际完成一次 Hermes → Bridge → HA 状态读取及文字回复；HA 实体、状态、服务、历史四个工具另行读取验证通过，控制、规则和任务均在传输前被拒绝。生产容器未重启，地址未切换。

生产 HA 主配置没有显式声明 Recorder 的 365 天、auto_purge 或 auto_repack；它可能在 include 文件或使用默认值，未据此假定实际保留期。只读基线：数据库及日志合计 23,544,800 字节，可用磁盘约 177.3GB，单次 24 小时历史工具查询约 14ms。统计只代表本次传感器读取，不是全家庭性能承诺。365 天部署仍需选择正式实体白名单、合并配置及维护窗口，本轮未修改生产 Recorder。现有 `scripts.recorder_plan` 可生成待部署方案；模板的空实体白名单不能当作正式选择。

## 2026-11-01 生产启用记录

用户确认启用全部按需功能后，生产 `xiaoai-ai` 已将 `HA_READ_ONLY` 设为 `0`，因此控制、规则和定时工具可见；实际动作仍受实体白名单、唯一选路、确认、状态回读和审计约束。本次没有提交任何设备动作、规则确认或定时确认。

已备份并应用 Home Assistant Recorder 365 天配置：仅 include `sensor.dmaker_cn_657218847_p28_temperature_p_9_1`，排除 RSS/linkquality 传感器，`auto_purge` 与 `auto_repack` 开启。配置校验和重启后校验通过。服务器备份位于 `/mnt/data/xiaoai-native-wake/backups/device-safety-20261101T053321Z/`；早期“未应用”段落是历史记录。
