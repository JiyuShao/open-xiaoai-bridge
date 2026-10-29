# 小爱豆包静默验收

2026-10-29，全部使用 Mock；没有真实 Hermes、HA、豆包合成、音箱或真人声学证据。

隔离入口：`docker-compose.doubao-validation.yml`。依赖镜像为服务器现有的 `xiaoai-native-wake:validation`；源码只读挂载，产物独立绑定。设置 `VALIDATION_SOURCE` 与 `VALIDATION_ARTIFACTS` 后运行该 Compose，产物目录须允许 UID 65534 写入。容器为非 root、无网络、无 capabilities、只读根目录、无 Docker socket 或生产目录、无生产凭据。隔离探针先验证无启用的非回环网卡/路由，并实测生产端口与公网均不可连接，才运行套件。内核可见但 DOWN 的隧道设备不等于可用网络。

服务器独立目录 `/tmp/xiaoai-doubao-validation-20261029/`：`source/` 只读源码；`artifacts/summary.json`、`unittest.log`、`recovery-result.json` 为可复查产物。第一轮发现依赖镜像继承启动开关使两项测试失败，入口已清理相关环境变量，重跑通过。

141 项测试，138 通过，3 项真实服务测试跳过。第二次新容器 hostname 为 `83d8b6b07a32`，恢复了首次容器 `6d7c0bf12d38` 的会话、历史、审计及日志；在途操作变成 `unknown/process_restarted`，未重放。

`tests/silent_client.py` 只应答并记录设备 shell/录音/播放命令和 PCM 字节数，绝不执行。文件音频入口使用真实对话协调逻辑和显式 Mock 数值模型、Hermes/TTS HTTP 协议。尚未验证 Rust WebSocket 与真实 Client 的整链协议，也未验证真实识别准确率、音色听感、远场或回声。Mock 的“完成”仅表示替身状态已观测。

生产容器 `xiaoai-ai`、现有 HA 和 Hermes 不做部署、重启、配置修改或连接切换。真实接线另行进行；缺少凭据不影响 Mock 阶段交付。

## HA 扩展最终验收

本地最终套件 167 项：164 通过、3 项真实服务跳过。新增服务与脚本 11 个模块 mypy 通过；既有全项目类型问题不计为本次已修复。

覆盖唯一原生/HA选路、模型不能绕过高风险确认、实体权限和凭据脱敏、状态回读、历史统计与午夜边界、365天Recorder方案、场景/自动化提案和修改/删除确认、已确认自动化提醒事件、多动作独立去重、定时任务与错过/重启恢复。文件音频8轮案例串联开灯→历史→场景提案→下一轮确认→提醒提案→下一轮确认→场景执行→“不用了”退出，同一默认session；VAD/ASR使用显式模型替身，不能作为识别准确率证据。

Standards/Spec 两轴审查最终无未解决项。已修复审查发现的强制HA绕过、原生高风险绕过、原生决策缺审计、失效实体/错误状态规则、每日任务重启丢后续计划、跨轮提案ID不可获得、自动化多提醒误去重和午夜统计越界。当前session每次新提案会取代旧待确认项，模型不能用模糊确认执行旧提案。

可重复构建测试包：

```sh
python -m scripts.build_silent_bundle --output /tmp/xiaoai-doubao-mock.tar.gz
```

此包包括启动回归需要的公开 config 和 Dockerfile，排除模型、私有环境文件、运行库和身份数据。服务器独立目录为 `/tmp/xiaoai-doubao-final-20261029/`，不覆盖生产项目目录。中间失败包含打包漏掉测试配置和Dockerfile，均属未完整测试素材，最终结果以目录内最新 summary.json 与 unittest.log 为准，不隐藏失败记录或把失败计为通过。

真实接线仍待后续：Hermes profile安装与工具权限、实际模型记忆/声音元数据、HA配置API兼容性与自动化触发、Recorder365应用与真实容量基线、豆包真实合成、Rust WebSocket真实Client、音箱/声学/听感。当前Mock完成不代表这些项目已通过；未执行生产部署、配置写入、设备命令、播放或push。

最终服务器完整套件同为167项（164通过、3跳过），summary.success=true；容器d4811acdcd3d，2026-10-29 13:14:26 UTC完成。额外12项启动测试通过，覆盖HA错误配置在设备监听前失败。

最终重建恢复容器 `361e4ba0efc1` 通过，会话与产物保留、未知操作不重放。服务器 `artifacts/recorder-plan/` 留存 Recorder 方案与 Mock 基线：5764行、样本库348160字节、查询6.42–7.35毫秒、测试目录可用磁盘8237137920字节。这些仅是隔离Mock样本和测试盘数据，真实HA数据库/生产盘均未测量、配置未应用。

生产前后 StartedAt 完全一致：xiaoai-ai 为2026-10-27，HA为2026-10-17，Hermes为2026-10-28；测试后均未重启。生产容器状态快照位于同一独立目录的 production-before.txt / production-after.txt。Mock测试容器均使用 --rm 自动移除，产物仍保留。
