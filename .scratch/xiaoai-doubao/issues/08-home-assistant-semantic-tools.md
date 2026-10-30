# 08: Home Assistant 连接与语义工具边界

**What to build:** 复用已经运行的 Home Assistant 和 Hermes，为小爱豆包提供受限的 Home Assistant 语义工具和只读查询能力。工具可以读取实体、状态、服务和历史查询所需的元数据，但不能获得 shell、Docker、文件系统或任意 Home Assistant 管理权限。

**Blocked by:** 01: 小爱豆包文字对话与默认会话；03: xiaoai-bridge 原生指令与操作记录闭环

Status: ready-for-agent
Execution: read-only-live-adapter (2026-10-30); write-services-and-profile-pending

- [ ] Hermes 使用专用 profile 获得明确列出的 Home Assistant 工具，工具之外的能力不可见或不可调用。
- [x] 读取实体和状态不需要确认；会改变设备状态的调用能标记为需要确认的副作用。
- [x] Home Assistant 凭据只来自运行时私有配置，不进入提示词、操作记录或普通日志。
- [x] 工具调用无法访问音箱 shell、宿主机 Docker、任意服务器文件或未列入允许范围的 Home Assistant 管理接口。
- [x] 使用假的 Home Assistant 和假的音箱完成权限、错误和凭据脱敏测试，不连接生产音箱。

## Comments

2026-10-29：按用户“暂时全部使用 Mock”执行。软件实现与协议替身验收完成；勾选项表示 Mock 证据，不表示生产服务或真实设备已完成。实际 Hermes profile、HA 配置/触发、Recorder365部署与真实 TTS 均待专用环境验证。实现与限制见 docs/home-assistant-tools.md；静默验收见 docs/doubao-silent-validation.md。

2026-10-30：根据用户调整，真实 Hermes 与 Home Assistant 只读路径改为运行时接入；`HomeAssistantService(read_only=True)` 仅暴露实体、状态、历史和服务列表，拒绝控制/规则/任务并跳过后台监听。新增 `scripts/verify_live_readonly.py`，凭据只从环境读取，默认不发送聊天；`--chat-text` 仅发送无工具连通性请求。写设备、写规则、定时执行和音频仍使用 Mock，真实 Hermes profile 安装仍待确认。
