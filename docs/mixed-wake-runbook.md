# 原生小爱与小爱豆包混合唤醒

实现对应[任务 04](../.scratch/xiaoai-doubao/issues/04-mixed-routing.md)。这是本地软件验证说明，未更改现有生产配置、未部署、未连接真实音箱。

## 模式与旧开关迁移

| WAKE_MODE | 行为 | 默认配置 |
| --- | --- | --- |
| legacy（未指定时的默认） | 保留上游后端开关与 before_wakeup 路由 | config.py |
| native | 仅自定义词唤起原生小爱，禁用外部后端与 HTTP API | config.native-wake.py |
| mixed | 显式词表分流到原生和 OpenAI-compatible Hermes | config.mixed-wake.py |

未指定 WAKE_MODE 时，旧 `NATIVE_WAKE_ENABLE=1` 仍选择 native；旧开关为假则选择 legacy。同时指定新旧开关时，旧开关的真值须与“新模式是否为 native”一致；建议迁移时删除旧开关。例如 `WAKE_MODE=mixed NATIVE_WAKE_ENABLE=1` 会直接失败。

混合模式要求 `OPENAI_ENABLE=1`、音频输入开启；小智、OpenClaw（含旧别名）、QwenPaw 必须关闭，HTTP API 可选。纯原生的严格互斥规则保持有效。`CONFIG_PATH` 可以覆盖默认配置，但必须通过相同检查。

## 路由与切换

默认 `wakeup.routes` 为 `理想同学 → native`、`豆包豆包 → openai`。必须覆盖全部关键词，且同时包含这两种目的地。混合模式不运行 `before_wakeup`，不把普通原生 ASR 当成外部指令。

主循环和设备状态仍由 MainApp 管理；XiaoAI 回调使用同一事件循环，连续对话任务由现有 WakeupSessionManager 持有。切换锁只保护短暂的路由切换，不锁住整段对话。切回原生前，停止并等待旧对话完成清理，再发送一次原生唤醒请求；重复豆包唤醒不会重建会话。

豆包等待完整用户讲话时恢复 KWS；ASR、等待模型和播放阶段暂停 KWS。原厂“小爱同学”的空 RecognizeResult 唤醒标记仍能中断豆包任务与它持有的播放 token，恢复录音。普通原生指令与播放状态仍透传，不启动 Bridge 的原生连续对话增强。

自定义词在真实播报期间的可靠打断、真实麦克风识别率和设备端业务完成均不由这些测试保证。播报中的首选打断入口仍是原厂“小爱同学”；硬件行为待用户允许后实测。

KWS 与会话 VAD 使用独立的 Silero 循环状态。混合模式的路由与模型配置在启动时固定，修改后需重启并重新检查；本模式不启动配置热重载线程，避免已启动的模型和路由在运行中失配。

## 静默预检

需 Python 3.12+ 和项目依赖。环境示例见 [.env.example](../.env.example)，这些值不会自动加载。私有环境提供音箱 Token、Hermes URL/API key 与 TTS 凭据，勿提交仓库。

```bash
bash scripts/mixed-wake.sh --check
# 或使用已有环境：
WAKE_MODE=mixed OPENAI_ENABLE=1 python main.py --check
```

检查不联网、不监听端口、不生成语音。需要 KWS/VAD 六个非空文件，以及本地 SenseVoice 模型和 tokens；缺少模型不会自动下载。真正启动时先生成双关键词词表，并同步初始化 KWS、VAD、ASR 和后端配置，成功后才开放设备服务。模型加载失败会释放已创建的线程和 reader。

需要持久化默认会话：可写 profile 使用 `agent:xiaoai-doubao:default`，HA 只读合同使用 `agent:xiaoai-doubao:readonly`。`X-Hermes-Session-Key` 只隔离记忆作用域，Hermes 活动 profile 仍须在服务端全局配置；恢复历史和真实 profile 权限需在独立环境验证。

[混合容器示例](../docker-compose.mixed-wake.yml)默认只执行 `--check`，采用独立名称、测试 Token、卷和内部网络，不替换生产。Dockerfile 已包含两份模式配置；本次未构建或启动镜像。真实部署、网络放行和凭据配置不属于本次执行。

## 文件音频与软件测试

```bash
WAKE_MODE=mixed OPENAI_ENABLE=1 python scripts/check_mixed_wake_audio.py --cases /path/to/cases.json
```

清单格式：

```json
[
  {"file": "lixiang.wav", "expected": ["native"]},
  {"file": "doubao.wav", "expected": ["openai"]},
  {"file": "ordinary.wav", "expected": []}
]
```

文件须为非空 16kHz、单声道 PCM16 WAV。检查走真实 KWS/VAD 文件输入线程，但把派发出口替换为内存收集器；不导入设备服务，不发起 Hermes/TTS 请求，不执行 shell、不播放音频。输出含识别词、实际路由和预期路由。它需要本地模型与预检环境，未提供实际模型时不能声称通过声学验收。

```bash
python -m unittest tests.test_mixed_wake tests.test_native_startup tests.test_native_wake -v
```

自动测试以模型替身、合成帧 WAV、假的设备传输和 HTTP/TTS 边界验证启动、分流、重复请求、会话清理及打断。替身识别的是确定性帧标记；这是软件管线证据，不能证明“豆包豆包”在真机上的识别效果。语音 turn 的操作反馈、音乐让出和声音指令传递由后续任务 05 验证。
