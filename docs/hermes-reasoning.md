# Hermes 推理级别边界

## 当前生产版本核对

生产镜像为 `1Panel/hermes-agent:2026.5.16`。在容器内核对源码和只读 API 后，确认：

- `agent.reasoning_effort` 由 Hermes 的 `config.yaml` 读取，支持 `none`、`minimal`、`low`、`medium`、`high`、`xhigh`。
- Hermes 原生 `/reasoning <level>` 命令存在，默认是当前会话级覆盖；`/reasoning reset` 清除当前会话覆盖并恢复配置默认值。
- `--global` 会写入全局配置，不能由 Bridge 语音通道隐式触发。
- 生产 `/v1/chat/completions` 当前只读取服务端配置，没有实现 `model_options.reasoning_effort`；`/v1/capabilities` 也没有声明该字段或会话 reasoning 控制端点。

因此小爱豆包的默认值应由 Hermes Agent 配置设置为 `agent.reasoning_effort: low`。Bridge 的 Chat Completions 请求不发送 `reasoning_effort`、`model_options` 或其他推理覆盖，避免按问题复杂度替用户切换。

## 会话边界

通过 Hermes 原生控制面明确发送 `/reasoning high`、`/reasoning low` 等命令时，只改变当前 Hermes Session；该覆盖持续到该 Session 结束、执行 `/reasoning reset`，或 Hermes 重启清空内存覆盖。`/reasoning --global` 属于全局配置操作，不由 Bridge 自动执行。

切换推理级别不会改变 `X-Hermes-Session-Key` 或 `X-Hermes-Session-Id`。新建会话会清掉会话级覆盖并回到 `low`。

当前 Bridge 使用的是 Hermes 原生 `/v1/chat/completions`，旧镜像没有一个可供 Bridge 调用的安全会话控制端点。直接把 `/reasoning high` 当聊天文本发送可能被 Agent/工具解释成配置操作，甚至留下全局修改，所以 Bridge 现在会拦截精确的 `/reasoning` 和明确中文切换命令，返回“本次没有修改”，不把它交给 Hermes。普通问题仍原样发送，Bridge 不做复杂度判断。需要真正切换时，在 Hermes 原生控制面明确执行 `/reasoning <level>`；如果未来 Hermes API 暴露受鉴权的会话控制端点，再增加显式能力探测和映射，默认仍保持不传覆盖。

一次隔离探针确实观察到原始 `/reasoning high` 文本可能导致配置变更；该临时 high 值已恢复为 low，并在最终 Hermes 重建后再次确认运行时配置为 low。这个结果是保留 Bridge 拦截的原因。

## 已做的静默核验

2026-10-31 对生产 API 做了只读检查：`/health` 返回 200，`/v1/models` 鉴权成功，返回 `hermes-agent`；最小无工具 Chat Completions 请求约 1.16 秒。临时请求没有播放音箱、调用 Home Assistant 或修改生产会话。

生产配置切换前会保存 `config.yaml` 和 Compose/镜像回退信息；切换后只验证 Hermes/Bridge 健康、API 鉴权、默认 `low` 配置和 Session ID/Key 保持不变。

## 2026-10-31 部署结果

已在服务器应用 `agent.reasoning_effort: low` 并重建 Hermes 容器；镜像仍为 `1panel/hermes-agent:2026.5.16`，没有修改 Hermes 镜像。Bridge 最终切换到基于原稳定镜像的轻量补丁 `xiaoai-ai:reasoning-low-20261031-r2`，只替换 reasoning 拦截和混合配置文件，没有重新编译 Rust。此前完整重建 `xiaoai-ai:reasoning-low-20261031` 曾因服务器拉取 Rust Git 依赖的 SSL 网络错误失败，未使用该失败产物。

部署后核验通过：Hermes `/health` 为 200，未鉴权 `/v1/models` 为 401，带鉴权 `/v1/models` 为 200 且返回 `hermes-agent`；运行时 `GatewayRunner._load_reasoning_config()` 返回 `{'enabled': True, 'effort': 'low'}`。临时隔离 Session 的两次 Chat Completions 均为 200，第二次沿用相同 `X-Hermes-Session-Id`；Session Key/ID 没有因配置切换而更换。Bridge/Hermes 均运行中、重启次数为 0，Bridge 最近日志没有 Traceback 或 ERROR。

回退快照保存在服务器 `/mnt/data/xiaoai-native-wake/releases/reasoning-low-20261031-162138/`。
