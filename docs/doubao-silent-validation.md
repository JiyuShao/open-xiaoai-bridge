# 小爱豆包静默验收

2026-10-29，全部使用 Mock；没有真实 Hermes、HA、豆包合成、音箱或真人声学证据。

隔离入口：`docker-compose.doubao-validation.yml`。依赖镜像为服务器现有的 `xiaoai-native-wake:validation`；源码只读挂载，产物独立绑定。设置 `VALIDATION_SOURCE` 与 `VALIDATION_ARTIFACTS` 后运行该 Compose，产物目录须允许 UID 65534 写入。容器为非 root、无网络、无 capabilities、只读根目录、无 Docker socket 或生产目录、无生产凭据。隔离探针先验证无启用的非回环网卡/路由，并实测生产端口与公网均不可连接，才运行套件。内核可见但 DOWN 的隧道设备不等于可用网络。

服务器独立目录 `/tmp/xiaoai-doubao-validation-20261029/`：`source/` 只读源码；`artifacts/summary.json`、`unittest.log`、`recovery-result.json` 为可复查产物。第一轮发现依赖镜像继承启动开关使两项测试失败，入口已清理相关环境变量，重跑通过。

141 项测试，138 通过，3 项真实服务测试跳过。第二次新容器 hostname 为 `83d8b6b07a32`，恢复了首次容器 `6d7c0bf12d38` 的会话、历史、审计及日志；在途操作变成 `unknown/process_restarted`，未重放。

`tests/silent_client.py` 只应答并记录设备 shell/录音/播放命令和 PCM 字节数，绝不执行。文件音频入口使用真实对话协调逻辑和显式 Mock 数值模型、Hermes/TTS HTTP 协议。尚未验证 Rust WebSocket 与真实 Client 的整链协议，也未验证真实识别准确率、音色听感、远场或回声。Mock 的“完成”仅表示替身状态已观测。

生产容器 `xiaoai-ai`、现有 HA 和 Hermes 不做部署、重启、配置修改或连接切换。真实接线另行进行；缺少凭据不影响 Mock 阶段交付。
