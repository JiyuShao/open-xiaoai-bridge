# 08: Home Assistant 连接与语义工具边界

**What to build:** 复用已经运行的 Home Assistant 和 Hermes，为小爱豆包提供受限的 Home Assistant 语义工具和只读查询能力。工具可以读取实体、状态、服务和历史查询所需的元数据，但不能获得 shell、Docker、文件系统或任意 Home Assistant 管理权限。

**Blocked by:** 01: 小爱豆包文字对话与默认会话；03: xiaoai-bridge 原生指令与操作记录闭环

Status: ready-for-agent
Execution: software-complete; production-ha-allow-all-deployed (2026-11-01); read-only post-deploy-verified

- [x] Hermes 使用专用 profile 获得明确列出的 Home Assistant 工具，工具之外的能力不可见或不可调用。
- [x] 读取实体和状态不需要确认；会改变设备状态的调用能标记为需要确认的副作用。
- [x] Home Assistant 凭据只来自运行时私有配置，不进入提示词、操作记录或普通日志。
- [x] 工具调用无法访问音箱 shell、宿主机 Docker、任意服务器文件或未列入允许范围的 Home Assistant 管理接口。
- [x] 使用假的 Home Assistant 和假的音箱完成权限、错误和凭据脱敏测试，不连接生产音箱。

## Comments

2026-10-29：按用户“暂时全部使用 Mock”执行。软件实现与协议替身验收完成；勾选项表示 Mock 证据，不表示生产服务或真实设备已完成。实际 Hermes profile、HA 配置/触发、Recorder365部署与真实 TTS 均待专用环境验证。实现与限制见 docs/home-assistant-tools.md；静默验收见 docs/doubao-silent-validation.md。

2026-10-30：根据用户调整，真实 Hermes 与 Home Assistant 只读路径改为运行时接入；`HomeAssistantService(read_only=True)` 仅暴露实体、状态、历史和服务列表，拒绝控制/规则/任务并跳过后台监听。新增 `scripts/verify_live_readonly.py`，凭据只从环境读取，默认不发送聊天；`--chat-text` 仅发送无工具连通性请求。写设备、写规则、定时执行和音频仍使用 Mock，真实 Hermes profile 安装仍待确认。

2026-10-31：software-complete; real-hermes-bridge-ha-readonly-validated; production-not-deployed。软件交付与生产部署分开记录；Hermes 使用独立纯文字合同、空服务器工具集，实际工具由 Bridge 白名单执行。验证和未执行项见 `docs/doubao-silent-validation.md` 的 2026-10-31 节；没有调用真实音箱、HA 写操作或重启生产服务。

2026-10-31 最终收尾：用户允许低音量真机与卧室台灯测试后，已部署原生Hermes直连最终Bridge，完成自动验收。台灯最终off；Hermes/HA镜像与进程未变，生产HA只读、常驻记录关闭。原文字适配器/空工具集为已废弃的历史实现；以ADR-0005及 [最终报告](../../../docs/automated-acceptance.md) 为准。真实与Mock界限及真人待验收项见该报告。

2026-11-01：用户选择方案 3，启用 `HA_READ_ONLY=0` 与 `HA_ALLOW_ALL=1`。新增 `ha_call_service` 的任意 domain/service/data 调用、统一操作审计与请求去重；allow-all 下实体和 HA 服务列表不再依赖白名单，规则/定时校验同步支持任意 domain。生产 Bridge 切换至 `xiaoai-ai:ha-unrestricted-20261101`，健康、工具清单、双网络和零重启只读核验通过。一次台灯开关验证已在用户要求停止后关闭并回读 `off`；后续不再执行真实设备操作。
