---
name: xiaoai-bridge
description: 提交原生小爱指令、播报通知、查询音箱状态或打断播放时使用本项目的受控 HTTP 工具，并查询持久化操作记录。
---

# XiaoAI Bridge

通过 `tools/xiaoai-bridge` 调用语义接口。正式动作名为 `xiaoai_command`、`xiaoai_announce`、`xiaoai_status`、`xiaoai_interrupt`。先确认 `OPENXIAOAI_BASE_URL` 指向本次授权的 Bridge；测试必须指向隔离的假服务，不继承生产工具或音箱连接。缺省地址为本机 `http://127.0.0.1:9092`，不是测试隔离的保证。

## 执行步骤

1. 普通聊天由对话层自动播报，不为每句回复调用工具。操作请求先确定目标和路径；明确低风险请求直接执行，高风险、歧义、跨设备或后台规则请求先确认。HA 能力在后续任务接入，本工具目前只提交原生请求，不能同时向两条路径发令。
2. 每次新的有意动作生成一个请求 ID，使用当前 session 标识提交。原生指令固定静默，由发起请求的助手反馈。原生反馈抑制尚待真机验证。
3. 读取返回的 `operation.status`、`confirmation`、`response` 和 `message`。`accepted` 只代表接口受理；原生指令和播报均不能仅据此报告业务完成。`completed` 目前仅用于读取本地缓存状态（`confirmation=cached_status`、`response.cached=true`），不证明设备在线或实时状态。
4. 超时、断线、取消或重启后的 `unknown` 不自动重试。通过原请求 ID 查询记录；重复投递沿用原 ID，新的有意操作使用新 ID。同 ID 携带不同参数会拒绝，不会发送第二次。

## 调用

在 skill 目录执行；将示例请求 ID 替换为本次唯一 ID。

```bash
./tools/xiaoai-bridge xiaoai_command "打开客厅灯" --request-id req-123 --session-key agent:xiaoai-doubao:default
./tools/xiaoai-bridge xiaoai_announce "收到" --request-id req-124 --context "请温柔地说" --timeout-ms 120000
./tools/xiaoai-bridge xiaoai_status --session-key agent:xiaoai-doubao:default
./tools/xiaoai-bridge xiaoai_interrupt --request-id req-125
./tools/xiaoai-bridge operation req-123
./tools/xiaoai-bridge operations --session-key agent:xiaoai-doubao:default
```

`--help` 提供声音参数和诊断命令。工具使用 HTTP JSON 编码，保留指令中的引号和换行，不提供 shell、任意文件或 URL 播放。唤醒仅保留为 Bridge 内部调度 API，不作为 Hermes 自由调用的动作。

## 记录与证据

所有上述动作先落库再执行，记录保留至用户明确要求删除。`parameters.evidence=mock` 是假设备测试，`device_interface` 是设备接口返回；两者均不自动证明业务完成。查询、列表和删除属于记录管理，不产生音箱动作。

仅在用户明确要求删除某条记录时执行 `./tools/xiaoai-bridge delete <request-id>`。在途记录不能删除；删除后该 ID 的去重保护也消失，不再用旧 ID 重试动作。

服务端凭据只从运行配置读取，工具请求不接受凭据。静默测试替换 TTS 和设备传输，不能仅靠 `silent=true` 判断隔离成立。真实 Hermes 的 profile 和工具权限仍需单独验证。

此入口替代旧的 `xiaoai-tts`；`command`、`announce`、`tts`、`status`、`interrupt` 是同一语义工具的简写别名，不应配置成独立工具。
