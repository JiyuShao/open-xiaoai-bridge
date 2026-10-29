# 14: 现有容器接线与全链路静默验收

**What to build:** 在不新建或重新部署 Home Assistant、Hermes 或 `xiaoai-ai` 的前提下，完成从“豆包豆包”到原生小爱/HA 的全链路静默验收，并输出可复查报告。测试环境不能连接真实音箱或修改生产容器。

**Blocked by:** 07: 小爱豆包核心链路服务器静默验收；13: 连续对话中的 Home Assistant 工作流与退出词

Status: ready-for-agent
Execution: completed-mock (2026-10-29); production-wiring-pending

- [ ] 复用现有已部署容器和持久化目录，不创建替代生产容器、不重刷音箱、不重启生产服务。
- [x] 测试 Bridge、Hermes、HA 工具和假音箱之间有独立的测试边界，测试工具不能访问生产音箱、生产 Bridge、宿主机 Docker 或生产秘密。
- [x] 静默验证覆盖路由、原生控制、HA 控制、历史查询、Recorder 策略、场景、自动化、定时任务、提醒、退出词和失败恢复。
- [x] 所有音频只落文件或进入测试接收器；任何真实音箱播放、麦克风控制和设备端副作用均列为未执行。
- [x] 报告区分离线测试、Mock、真实 Hermes、真实 Home Assistant、真实 TTS 和真实音箱证据，不把 Mock 成功描述为设备完成。

## Comments

2026-10-29：用户明确选择全部 Mock；当前范围覆盖独立测试容器，保留生产容器与持久目录，不做真实接线。第一项真实容器接线留待后续，不能以 Mock 冒充。详见 docs/doubao-silent-validation.md。

最终服务器167项（164通过、3真实服务跳过）、重建恢复与生产StartedAt一致性核对均通过；Standards/Spec审查无未解决项。Mock容器已自动移除，产物保留独立目录。
