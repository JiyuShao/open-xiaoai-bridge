# 原生唤醒模式运行手册

## 当前状态

本地分支 `feature/native-wake` 已实现 `NATIVE_WAKE_ENABLE=1` 独立模式。默认新词是用户确认的“理想同学”，命中后只调用 `SpeakerManager.wake_up(awake=True, silent=False)`。等原生反馈后再说指令。原厂词继续由设备处理。

已完成软件替身测试、Docker 镜像构建，以及两轮真实 KWS/VAD + 假 Client 静默验证。用户确认的新词为“理想同学”；合成阳性音频每轮触发 1 次，阴性音频每轮 0 次。尚未完成真人录音或音箱验收，没有刷机或安装 Client。

## 部署前准备

记录日：2026-10-24，Asia/Shanghai。

| 项目 | 当前状态 |
| --- | --- |
| 音箱型号 | 用户照片已确认小米小爱音箱 Pro LX06 |
| 当前固件 | 用户确认 LX06 当前版本为 **1.94.13**；与上游列出的 LX06 补丁版本一致，尚未验证本机安装与恢复 |
| 硬件修订、分区及备用系统 | 待确认 |
| SSH / Client / 改造历史 | 用户确认未刷机、未开启 SSH、未安装 Client；尚未访问设备 |
| 部署目标 | 用户现有 Docker 服务器（不使用 PVE）；iStoreOS 24.10.5、x86_64、4 核、约 16 GB 内存，Docker 27.3.1 / Compose v2.39.1，SSH 已验证 |
| 本地开发 | macOS，uv 可用；隔离测试 Python 3.12.13 |
| 本地 Docker | shell PATH 未检测到；镜像已在目标服务器构建并运行 |
| KWS/VAD 模型 | 配套包中六个 KWS/VAD 文件已提取并记录 SHA256；不挂载全文 ASR 模型 |
| Git | 独立 fork，本地分支 `feature/native-wake` |

设备端需从补丁固件与 SSH/Client 安装条件开始准备。下一步确认可恢复的原厂备份、备用系统与刷机工具，服务器验证可独立进行。固件版本一致仅解决版本匹配的一项条件，不代表已具备刷机条件。

Bridge 基线为 `a1f77a888f601c1536a14494d30c959cbf7a54e4`，保存在本仓 Git 历史。Client 候选版本为 `coderzc/open-xiaoai@14e1034ba75698be7d3f331b9822b7043deaf032`；二进制与本机兼容性待验证。

## 运行条件

- Python 3.12+、uv、上游 Python/Rust 构建所需系统依赖。完整构建要求沿用根目录上游说明。
- 服务器 `core/models/` 下准备匹配上游 KWS 的六个文件：`silero_vad.onnx`、`encoder.onnx`、`decoder.onnx`、`joiner.onnx`、`tokens.txt`、`bpe.model`。可从上游模型发布包中取得对应文件；来源与实际 SHA256 需记录并核验。仅文件存在和摘要一致不能证明格式兼容。
- 服务器和音箱 Client 配置一致的 `OPEN_XIAOAI_TOKEN`，限制 TCP 4399 只允许音箱访问。原生模式拒绝启用 HTTP API。
- 音箱具备经核验的 SSH/Client 安装条件；刷补丁固件前确认当前版本、原厂备份、备用系统和恢复工具。

当前模型、Client 二进制和固件均未在这台设备上验证。原生模式启动脚本在模型缺失时直接停止，不自动下载上游含全文 ASR 的整包。

## 离线检查

在本机或目标服务器的项目根目录执行：

```bash
bash scripts/native-wake.sh --check
```

此命令共用 `main.py --check`：检查模式、Token 是否设置、Python 配置是否可加载、词表和冷却/超时参数，以及六个非空模型文件并输出摘要。不会加载声学模型、生成词表、安装依赖或启动监听。检查优先使用 `.venv/bin/python` 3.12+，否则通过 uv 的离线缓存选择 3.12+；没有合适解释器时明确失败，不下载 Python。Token 值不会输出。自定义 Python 配置文件会被执行，应保持其顶层代码没有网络或设备副作用。

Token 通过本机私有环境设置，不发送到聊天、不写进版本库。仓库 `.env.example` 是字段说明；脚本不自动加载 `.env`。

## 准备就绪并获部署授权后启动

```bash
bash scripts/native-wake.sh
```

启动脚本设置原生模式，并将未设置的外部后端开关默认为关闭。命令行、旧检查命令和容器共用启动入口；开关统一接受 `1/true/yes/on`（忽略大小写和首尾空白），进入运行时后规范化为 `1/0`。如果环境已经启用小智、OpenClaw（包括旧别名）、OpenAI、QwenPaw 或 HTTP API，会报错停止，避免隐式接管。麦克风输入必须开启。

此命令先通过 uv 同步完整上游构建依赖，再由 `main.py` 检查配置与模型、原子生成词表、同步加载 KWS/VAD，最后启动 Bridge 并监听 Client；不自行安装音箱端 Client，也不刷固件。本次通过容器 CMD 执行同一 `main.py` 启动链，未在宿主机安装 Python/Rust 运行环境。

容器需从本分支构建镜像，不能直接使用不含本地修改的上游 `latest`。镜像已包含原生配置，CMD 直接使用项目 Python 执行 `main.py`，与命令行共享准备逻辑。设 `CONFIG_PATH=/app/config.native-wake.py`、`NATIVE_WAKE_ENABLE=1`、`AUDIO_INPUT_ENABLE=1`、`OPEN_XIAOAI_TOKEN` 并关闭其余后端及 API；将模型目录挂载到 `/app/core/models`。静默验证使用 `docker-compose.native-wake.yml`，仅绑定回环端口、禁止外连、关闭自动重启。真实设备部署须另行配置允许音箱访问的地址和访问控制。

## 修改唤醒词

修改独立配置 `config.native-wake.py` 中的 `APP_CONFIG["wakeup"]["keywords"]`。原生模式的统一启动入口默认选择此文件，已有 `CONFIG_PATH` 则保留；单独执行关键词生成脚本时需明确设置 `CONFIG_PATH`。上游 `config.py` 不随模式改变，也不会覆盖用户词表。关键词列表与检测文件须同步，修改后完整重启启动脚本以重新生成 `keywords.txt`；不要只依赖配置热重载。英文按上游要求使用小写。

`native_wake.cooldown_seconds` 默认 3 秒，从调用结束后开始冷却；`timeout_seconds` 默认 5 秒。一次调用仍在等待时会拒绝并发唤醒；超时不代表设备一定未执行，不自动重试。两项参数必须为有限正数。它们不能替代真实声学回声测试。

## 行为边界

- 原生模式绕过 `before_wakeup` 示例 hook、外部 ASR 接管、停止原生播放和 Bridge 连续对话增强。
- KWS 复用自身的 Silero VAD 门控，不启动外部对话 VAD 线程，不预热全文 ASR，也不查询小智 OTA 或改写其设备 ID。
- 原版 Rust 服务在 Client 连接后仍可能播放“已连接”提示；没有将其改成额外的 Agent 播报。
- 模型或关键词准备失败时返回非零，且不启动设备监听；生成词表为空时保留旧词表并停止启动。原生模式退出会停止 KWS、注销输入流、取消 Python 服务任务并回收事件循环与线程。设备服务异常或意外返回会让进程失败退出。
- 原生模式的 HTTP API 默认关闭，显式设为开启同样拒绝；本期不提供手动开启例外。外部 AI 模式保留原有 API 配置。
- 普通外部 AI 模式仍沿用启动脚本的模型下载行为，关键词生成交给统一入口；原生模式不走该下载分支。
- 离线单元测试使用声学运行时和设备传输替身；服务器集成验证使用真实模型与 Rust 服务，仅 Client 为替身，不能代替真机验收。

## 验证

在安装测试依赖的 Python 3.12+ 环境中，从项目根目录执行：

```bash
python -m unittest discover -s tests -p test_native_wake.py -v
python -m unittest discover -s tests -p test_native_startup.py -v
python -m unittest discover -s tests -p test_wakeup_keywords.py -v
python -m unittest discover -s tests -p test_audio_stream.py -v
# 全量自动化 unittest（真实服务测试默认跳过）
OPENCLAW_LIVE_TEST=0 python -m unittest discover -s tests -v
```

原生测试使用真实 MainApp → NativeWakeController → SpeakerManager → XiaoAI 分流与设备调用链，仅替换设备传输和数值/声学运行时；覆盖自定义词、原生命令透传、并发与冷却、拒绝/异常/超时、显式配置及延迟初始化。原有外部模式仍经过原有 hook，会话管理器只负责外部对话。

启动入口读取原生模式一次并传给应用；XiaoAI 使用应用设置的事件处理策略，不逐事件读取环境变量。ConfigManager 加载与重载不依赖原生模式；只有实际创建 XiaoZhi 时才初始化其身份与 OTA。

2026-10-24：原生分流测试 12 项、启动集成测试 9 项通过；全量 unittest 84 项加 2 项 ASR 函数检查，共 83 项通过、3 项真实服务测试跳过。Python 编译、shell 语法与 diff 检查通过。未配置静态类型检查器；编译检查不代表类型检查。

此前本机缺模型与 Token 的失败检查已通过；本次服务器六个模型摘要检查通过，完整 Bridge 镜像已构建并运行。

### 启动集成验证

任务 02 增加临时项目中的真实入口测试，仅替换外部声学运行时和 Rust 设备传输。覆盖系统 Python 不可用时选择项目解释器、大小写/空白开关、Dockerfile 声明的运行文件与 CMD、缺模型与无效配置、空编码不替换旧词表、模型/服务失败和 SIGTERM 清理。容器测试通过本地执行其文件与命令验证启动契约，不能替代镜像构建。

此前的系统 Python `hashlib.file_digest` 异常、容器开关解析差异和容器遗漏原生配置已修复。真实模型推理和镜像构建现已验证；设备验收仍待完成。

### 真机验收

以下项目均未执行：新词唤醒及反馈、原厂词、天气、闹钟/计时器、米家控制、音乐、播放中唤醒、连续唤醒、播报回声、Client/Bridge 断线与重启、固件备份和恢复。先记录原厂功能基线，安装后逐项比较；全部实测通过才标记一期可用。

需求与范围见 [一期 spec](../.scratch/native-wake/spec.md)。

## 回退与固件恢复

停止 Bridge 只停止服务器上的新增唤醒入口，不会自动卸载音箱 Client、恢复启动项或刷回原厂固件。服务离线后原厂词及原生功能是否正常仍需真机验证。软件请求受理不代表设备完成唤醒。

[上游教程](https://github.com/coderzc/open-xiaoai/blob/14e1034ba75698be7d3f331b9822b7043deaf032/docs/flash.md) 的备用系统切换建立在只修改 system0、另一套系统完好的前提下，不是通用无损恢复保证。[补丁说明](https://github.com/coderzc/open-xiaoai/blob/14e1034ba75698be7d3f331b9822b7043deaf032/packages/client-patch/README.md) 说明原版镜像可用于刷回，但需匹配具体型号与版本。

在拆机、刷机、分区或启动项变更前，必须确认：

1. 本机硬件和真实分区，以及匹配补丁的来源与摘要。
2. 可恢复的原厂备份、备用系统状态、工具和线缆。
3. 断电或失败处理路径，以及 SSH 凭据和网络隔离。
4. 用户对具体设备操作步骤的授权。

当前没有执行音箱安装、OTA、固件写入或启动分区切换。

## Docker 服务器静默验证（任务 04）

服务器工作目录为 `/mnt/data/xiaoai-native-wake/`，源码在其 `source/` 下，六个模型在 `models/` 下。使用 Docker 数据盘，避免占用约 1.9 GB 的系统分区；未修改现有容器或路由配置。`validation.env` 权限为 600，Token 在服务器生成，不写入 Git。源文件和测试音频不含凭据。

验证容器名为 `xiaoai-lixiang-wake-test`（小爱音箱的“理想同学”唤醒测试）。

编排限制：端口 `127.0.0.1:14399`、独立 internal 网络、2 核 / 2 GB 内存、无自动重启、日志轮换。该编排用于无真机验证。默认重启策略为 `no`，避免异常时循环启动；手动停止与重启如下，均在服务器源码目录执行：

```bash
docker compose --env-file ../validation.env -f docker-compose.native-wake.yml build
docker compose --env-file ../validation.env -f docker-compose.native-wake.yml run --rm --no-deps bridge /app/.venv/bin/python main.py --check
docker compose --env-file ../validation.env -f docker-compose.native-wake.yml up -d
docker compose --env-file ../validation.env -f docker-compose.native-wake.yml stop
docker compose --env-file ../validation.env -f docker-compose.native-wake.yml start
```

镜像使用 Python 3.12、Rust 1.90 工具链，Python 依赖由 `uv.lock` 锁定，Rust 依赖使用已有 `Cargo.lock` 并以 `--locked` 构建，其中 `open-xiaoai` Git 依赖固定为上述 Client 候选提交。uv 固定为 0.7.22；基础镜像标签仍可能更新，不要把不同时间重新构建的镜像视为相同制品，验收须记录实际镜像 ID。

模型来源：[Bridge 配套发布包](https://github.com/coderzc/open-xiaoai-bridge/releases/download/vad-kws-asr-models/models.zip)。该发布地址可变，以以下摘要识别本次文件。服务器只上传六个文件；全文 ASR 未部署。官方新版中英 KWS 使用拼音词表，不能直接替换当前 `cjkchar+bpe` 模型。模型挂载目录需允许容器用户写入生成的 `keywords.txt`；本次上传保留 Mac UID，已将专属 `models/` 目录所有者修正为服务器 root（UID 0），无需增加容器权限。

| 文件 | SHA256 |
| --- | --- |
| models.zip（本地下载缓存） | `8e8a709d6ea011f644f3c5055f536d5e6eb363eeafba53cc3232398fd636d273` |
| silero_vad.onnx | `2623a2953f6ff3d2c1e61740c6cdb7168133479b267dfef114a4a3cc5bdd788f` |
| encoder.onnx | `f559f422d970323e0c266aaa3c56cf6d1a3a4bc0c0822124727686c1e43e9447` |
| decoder.onnx | `2e3b5ec371f8899ee6acd829fd753ba45772df57a91bdf37cde3136354e7db7d` |
| joiner.onnx | `1ed689c5ed19dbaa725d9d191bb4822b5f4855a39e1ffd28cbc1f340d25b2ee0` |
| tokens.txt | `a8e0e4ec53810e433789b54a5c0134a7eaa2ffca595a6334d54c00da858841d3` |
| bpe.model | `bcae393dbc5611be5ffa4c7ae0841558978a5a4f484008cb9dff3a2cc97ebe01` |

`tests/native_wake_runtime.py` 是按需执行的集成验证工具，不属于默认离线测试。输入必须是非空、16 kHz、单声道、16 bit PCM WAV；先验证无 Token 和错误 Token 均被拒绝，再分别发送阴性与阳性音频，期待 0 / 1 次原生唤醒 RPC。假 Client 从不执行收到的 shell 命令，连接提示同样仅应答，因而不会播放声音。Token 从环境读取；在 Bridge 容器中执行可直接继承其环境，切勿手工回显。

把脚本和两段音频复制到验证容器的 `/tmp/` 后运行：

```bash
docker exec -e PYTHONPATH=/app xiaoai-lixiang-wake-test /app/.venv/bin/python /tmp/native_wake_runtime.py --uri ws://127.0.0.1:4399 --negative /tmp/negative.wav --positive /tmp/positive.wav
```

本次音频由 macOS Tingting 语音合成为文件：阳性“理想同学”（16,782 帧），阴性“今天阳光很好，我准备出门散步，晚上回来吃饭。”（81,817 帧）。未调用播放器。合成音频用于验证推理与传输链路，不能代替真人发音、远场麦克风、播放回声或真实音箱验收。

服务器直连部分依赖源缓慢，本次构建使用临时回环 SSH 下载通道；运行时不继承该代理，服务器全局网络配置未改变。重新构建需具备可用的 Docker/PyPI/crates.io/GitHub 下载链路；已有镜像启动不需要下载依赖。

### 本次实测结果

2026-10-24：镜像 ID 为 `sha256:7250e42fe50e26627199c4d7033a04c10fdef6e183b87be819108b174ef445ca`，标签 `xiaoai-native-wake:validation`。实际版本：Python 3.12.15、onnxruntime 1.23.2、sherpa-onnx 1.12.28、websockets 16.0。六个模型的服务器摘要与上述表格一致，真实模型成功加载，日志确认检测到“理想同学”。

首轮与停止/重启后第二轮结果一致：缺 Token / 错 Token 均返回 401；阴性合成语音 0 次唤醒 RPC，阳性合成语音 1 次。原生请求由假 Client 应答成功，不代表音箱已经唤醒；未执行收到的任何 shell 请求，也未播放连接提示。停止时退出码 0，未发生 OOM。

实际核验回环端口、internal 网络、2 核 / 2 GB 上限及 `restart=no` 均生效，运行环境不含临时构建代理。一次空闲采样约 244 MiB 内存。容器当前保持运行，临时构建下载通道已关闭，失败构建的临时容器已清理，原有容器继续运行。源码、模型与日志保留在专属数据目录；本次复测输出保存在其 `runtime-test.log`。真人录音、真机远场、原生功能共存与恢复验收仍未执行。
