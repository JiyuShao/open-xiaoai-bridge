# 原生唤醒模式运行手册

## 当前状态

本地分支 `feature/native-wake` 已实现 `NATIVE_WAKE_ENABLE=1` 独立模式。默认新词是用户确认的“理想同学”，命中后只调用 `SpeakerManager.wake_up(awake=True, silent=False)`。等原生反馈后再说指令。原厂词继续由设备处理。

已完成软件替身测试、Docker 镜像构建，以及两轮真实 KWS/VAD + 假 Client 静默验证。用户确认的新词为“理想同学”；合成阳性音频每轮触发 1 次，阴性音频每轮 0 次。2026-10-25 已在一台 LX06 上完成刷机、Client 安装与开机自启，真人新词唤醒、原生指令、原厂词和重启恢复通过首轮验收；播放中唤醒、远场与逐项原生功能对照仍待补，详见[真机安装与一期验收](#2026-10-25-真机安装与一期验收)。

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

设备端尚未操作；配套制品和 Mac 工具依赖已准备。下一步是接线后的只读识别、启动环境与分区核验、备份读取验证，以及原厂功能基线。固件版本一致仅解决版本匹配的一项条件，不代表已具备刷机条件。

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

## 真机前准备与明日流程

核验范围：LX06 1.94.13、当前未刷机/SSH/Client，资料截止 2026-10-24。按上游源码、发布 API、构建记录与本地文件实测核验；以下区分制品准备完成与本机恢复条件未知。用户已确认明日使用当前 Apple Silicon Mac，数据线与拆机工具均已有。

### 硬件与 Mac

上游 [LX06 接线说明](https://github.com/coderzc/open-xiaoai/blob/14e1034ba75698be7d3f331b9822b7043deaf032/docs/flash.md#L6-L24) 明确要求拆开外壳，连接主板内部的 Micro USB 调试口；数据线须能传数据。外露 Type-C、无需拆机的是 OH2P。今天未拆机或插接音箱。

已按 [Mac 工具说明](https://github.com/coderzc/open-xiaoai/blob/14e1034ba75698be7d3f331b9822b7043deaf032/packages/flash-tool/README.md) 安装 `libusb-compat 0.1.9`，核对工具 arm64 切片引用的 Homebrew 动态库存在。`update` 和 packer 均为 arm64/x86_64 universal；本机无需为原生 arm64 工具强制使用 Rosetta。尚未执行设备识别，动态库存在不等于 USB 访问或恢复已验证。

### 已下载的制品

本机缓存统一位于 `.cache/native-wake-preflight/assets/`，不进 Git。固件与 Client 均已下载并重新计算 SHA256，与发布 API 提供的 digest 一致；固件由 `file` 识别为 SquashFS v4 / xz。工具从固定上游提交提取，未执行。目录内 `SHA256SUMS` 可用于明天再次校验。

| 文件 | SHA256 |
| --- | --- |
| `LX06_1.94.13.squashfs` | `317a54909f12b63f22128965bf9005c06435d15f61c4202bb88c4524bf1e2ef4` |
| `LX06_1.94.13_patched.squashfs` | `3fbcfcd8a2130f5421d0bc3a3b22cb5a3de59b5d3678a3a14b23541aa9474225` |
| `client` | `bcf065e3809913f0dc7708c7487f91e31bc83e27b01f36b914f76fe6825876b5` |
| `flash-tool/flash` | `d801d8e393d7f452989a1c089584140b8f1467e91b7467547a1115d3069b361e` |
| `flash-tool/macos/update` | `19f37cc129d061512c24bffbbad3a51355d976c12d6091366fe7dfce3c949b30` |
| `flash-tool/macos/aml_image_v2_packer` | `3666083d441eb1abcb1e4a6a2c7ba89c2b134984d99afc1f203222080476c2e4` |

固件来源为 [idootop 的 LX06_1.94.13 发布](https://github.com/idootop/open-xiaoai/releases/tag/LX06_1.94.13)（2026-01-01），它也是 [固定版本补丁说明](https://github.com/coderzc/open-xiaoai/blob/14e1034ba75698be7d3f331b9822b7043deaf032/packages/client-patch/README.md) 指向的发布，不使用 coderzc 的另一个 1.88.206 固件。

Client 来源为 [coderzc 发布](https://github.com/coderzc/open-xiaoai/releases/tag/open-xiaoai-client)。该标签指向旧提交，但资产后来被覆盖更新；本次资产更新时间为 2026-04-01，不能凭标签日期判断其源码或鉴权支持。已核对 [成功构建 23848790049](https://github.com/coderzc/open-xiaoai/actions/runs/23848790049)：构建提交 `355bed514605586a24562306f7f2356dcc9cb34e` 的 `packages/client-rust` 目录树与固定版本 `14e1034…` 完全一致，均为 `e57c8b852ae7423b6a2647c46fe8b84b2a5661c4`。资产更新时间与构建完成时间吻合；二进制含 `OPEN_XIAOAI_TOKEN`、`Authorization` 和 `Bearer` 字符串。它是 ARM EABI5、动态链接 `/lib/ld-linux-armhf.so.3`，与源码的 ARMv7 hard-float 目标一致。这些是来源与兼容性线索，不是可复现构建证明或本台设备运行结果；无需据旧标签直接判定缺鉴权或重编译。明天仍需核验设备动态链接器和运行结果。

### 补丁与恢复的边界

固定版本的 [补丁源码](https://github.com/coderzc/open-xiaoai/tree/14e1034ba75698be7d3f331b9822b7043deaf032/packages/client-patch/patches) 除 SSH 外还修改 root/串口登录、阻止 OTA、让 `rc.local` 执行 `/data/init.sh`，并为 LX06 修改 ALSA 共享采音配置和 `libxaudio_engine.so` 的采音设备字符串。保留原生业务是上游主张与设计目的，不能理解为系统文件完全不变；特别要验收播放中采音与原厂功能。本次还用 `unsquashfs 4.7.5` 对已下载的两份镜像只读提取并比较 10 个目标文件，确认上述 SSH、OTA、启动钩子、两份 ALSA 配置及音频引擎确有变化；音频引擎原有 `hw:0,3` 字符串在补丁镜像中消失。这是定向检查，不是全镜像差异审计。详细文件摘要保存在本地缓存 `firmware-inspection.json`。

下载的“原版”是开源项目提供的根文件系统镜像，不是本台设备备份，也不是小米完整救砖包。它没有证明能够恢复本机身份信息、data、bootloader 或启动环境。备用系统切换只有在 system1/boot1 确实完好且与 data 兼容时才可能恢复；当前尚未读取本机这些信息。

[flash 包装脚本](https://github.com/coderzc/open-xiaoai/blob/14e1034ba75698be7d3f331b9822b7043deaf032/packages/flash-tool/flash) 仅提供连接、延时、切换和写分区功能，没有备份入口；它忽略底层部分退出状态并隐藏 stderr，因此“成功”文字不构成验证。底层二进制存在读取命令的帮助字符串，但未证明能在本机读取正确分区与长度。备份读取方式必须在接线后根据真实输出确认，不能假设当前无 SSH 的设备已能用 SSH 导出备份。

### 2026-10-25 操作顺序与停止条件

| 阶段 | 明天要做 | 进入下一阶段的条件 |
| --- | --- | --- |
| 1. 原厂基线 | 先用原厂词测试反馈、天气、音乐、闹钟/计时器；仅对已有且获授权的米家设备做安全操作，测试后清理临时闹钟等 | 记录实际结果；当前全为待测 |
| 2. 物理与只读识别 | 由用户完成必要拆机/接线；检查 Mac USB 枚举和设备识别；只读取得真实启动环境、分区名称/长度和现有系统信息 | 型号、USB 访问、真实分区与启动槽有证据，不能用包装脚本的成功文字代替 |
| 3. 备份与恢复核验 | 确认可行的只读导出方式，按实际长度保存；重复读回比对、计算摘要；私下保存可能含身份信息的分区数据 | 本机备份及备用系统/恢复路径有证据。读取能力或恢复条件仍不明时停止，保持不写入 |
| 4. 审阅设备操作 | 根据已取得的信息逐条确定目标分区、补丁与恢复步骤，由用户批准具体操作 | 尚未授权任何拆机代操作、分区写入、`saveenv` 或启动项变更 |
| 5. 受控安装与连接 | 仅执行已批准步骤；核验 SSH，修改默认凭据；检查 ARM 动态链接器；核验已缓存 Client 摘要后传入，首次前台运行 | 保留原始输出，确认真实采音与连接；异常立即停止，不跨版本刷写 |
| 6. 新入口与原生对照 | 安静环境先说“理想同学”，等反馈再说指令；与原厂基线逐项比较，再测播放中、远场、回声、断线与重启 | 按任务 06 逐项记录，不把 RPC 受理算成设备唤醒 |

当前容器仅绑定服务器 `127.0.0.1:14399` 且网络为 internal；音箱接入前须根据其真实 IP 规划受限 LAN 端口、网络和来源访问控制，再通过私有配置给 Client 相同 Token。该网络变更今天未执行；不要把测试编排当作现成真机接入配置。

首次 Client 验证不直接运行远端 `curl | sh`，也不直接覆盖 `/data/init.sh`。[init.sh](https://github.com/coderzc/open-xiaoai/blob/14e1034ba75698be7d3f331b9822b7043deaf032/packages/client-rust/init.sh) 会下载可变制品且不核验摘要；[boot.sh](https://github.com/coderzc/open-xiaoai/blob/14e1034ba75698be7d3f331b9822b7043deaf032/packages/client-rust/boot.sh) 依赖 ping 外网、在文件缺失时下载 Client 并隐藏日志。先用已核验制品前台确认效果，长期启动项留到真机验收后决定。停止 Bridge 不会撤销这些设备端修改。

### 扩展静默测试与已知限制

未改变 KWS/VAD 阈值。测试通过真实模型、Rust 服务和 WebSocket 传输，仅使用不执行命令的假 Client。语料由 macOS 26.5 的 Tingting/Meijia 合成为文件，覆盖语速 140/190/240、词首/句中呼叫、近似词、“小爱同学”、普通讲话、静音、白噪声和加噪阳性；全程未播放声音。

| 样本组 | 结果 |
| --- | --- |
| 纯语音阳性：两音色 × 三语速、两条带上下文呼叫 | 8/8 各触发 1 次 |
| 阴性：14 条近似词/其他讲话 + 静音/纯白噪声 | 16/16 均为 0 次 |
| 阳性 + 合成白噪声，整段 SNR 约 10 dB | 0/1，漏唤醒，原始测试退出码为 1 |
| 追加阳性 + 合成白噪声，SNR 约 15/20 dB | 2/2 各触发 1 次 |

共 27 个单次样本，26 项符合预期；10 dB 漏检保持失败，未修改期望值或调参掩盖。该样本不足以估计日常成功率；真实麦克风、距离、回声与环境声仍未验收。明天先在安静环境建立真机基线，再判断是否需要调整检测参数。

本机语料与定义保存在 `.cache/native-wake-preflight/corpus/`，服务器保存在专属数据目录的 `preflight/corpus/`。每个音频的摘要见同目录 `SHA256SUMS`；两个清单分别为 `cases.json`（25 项，SHA256 `05e93160aae527fe79988f81793d568cf86109bf53a22078d8f4e6d9495c494c`）和 `noise-cases.json`（2 项，SHA256 `0e6e95e40d1c7592e2e27f80aa2867c6749aa823020bd5384f0f056a674b6c42`）。原始服务器结果保留在 `preflight/runtime-corpus.log` 与 `preflight/runtime-noise.log`。

复制语料目录内容到验证容器 `/tmp/preflight-corpus/` 后，使用同一测试工具复测：

```bash
docker exec -e PYTHONPATH=/app xiaoai-lixiang-wake-test /app/.venv/bin/python /tmp/native_wake_runtime.py --uri ws://127.0.0.1:4399 --cases /tmp/preflight-corpus/cases.json
docker exec -e PYTHONPATH=/app xiaoai-lixiang-wake-test /app/.venv/bin/python /tmp/native_wake_runtime.py --uri ws://127.0.0.1:4399 --cases /tmp/preflight-corpus/noise-cases.json
```

第一条预期会报告已知的 10 dB 漏检并返回非零；第二条本次通过。工具会继续检查清单中其余样本，最终汇总失败，既有 `--negative` / `--positive` 双文件用法仍可用。

本轮本地回归检查共 86 项，83 项通过、3 项真实服务测试跳过；Python 编译与 diff 检查通过。27 项真实模型合成语音验证单独记录，保留 10 dB 样本失败，不计入离线回归通过数。

## 2026-10-25 真机安装与一期验收

设备：LX06，经 Windows 主机 Amlogic 工具（`update.exe`）走 USB 烧录模式安装。当前启动槽 `boot0` + 补丁 `system0`；`boot1` / `system1` 保持官方 1.94.13 未改动，作为回退槽。

| 步骤 | 实际结果 |
| --- | --- |
| 写入 system0 | `update.exe partition system0`，镜像 38,453,248 B；写后 `mread` 回读，SHA256 `3fbcfcd8…4225` 与镜像逐字节一致 |
| 启动项 | `setenv bootdelay 15`、`setenv boot_part boot0`、`saveenv` 均返回 success；该 CLI 不返回 `getenv` 输出，未实读现值 |
| 启动验证 | 重启后内核 4.9.61（boot0，2025-07-01 构建）；补丁 rootfs 生效：`rc.local` 挂接 `/data/init.sh`、dropbear 可用 |
| Client | `/data/open-xiaoai/client`，SHA256 `bcf065e3809913f0dc7708c7487f91e31bc83e27b01f36b914f76fe6825876b5`；`token.txt` 权限 600；`server.txt` 指向 `ws://192.168.1.2:4399` |
| 自启 | `/data/init.sh` 循环拉起 Client，断线 5 秒重试；重启音箱后自动重连已验证 |
| SSH 凭据 | 用户决定保留默认 `root` 密码，未修改；该项未完成，记录为已知风险 |

服务器：新增 `docker-compose.device.yml`（容器 `xiaoai-lixiang-wake`），仅绑定 `192.168.1.2:4399`；Token 由服务器 `/mnt/data/xiaoai-native-wake/device.env`（600）提供，未写入仓库或聊天。原有静默验证容器继续运行。

调试期间曾临时把容器 `LOGLEVEL` 设为 `DEBUG` 观察 VAD/KWS，验收后已恢复为默认 `INFO`。

### 一期验收记录

| 项目 | 结果 |
| --- | --- |
| 新词唤醒 | 通过。真人 1 米内连续两次说“理想同学”，KWS 均命中，设备执行 `ubus call pnshelper event_notify` 唤醒请求 |
| 唤醒反馈 | 设备给提示音，不播“我在/哎”；随后指令由原生小爱正常应答 |
| 后续指令 | 通过。唤醒后正常问答（未逐项记录天气、闹钟、音乐、米家） |
| 原厂词 | 通过。测试期间“小爱同学”及原生功能正常 |
| 多次唤醒 | 通过（连续 2 次） |
| 播报回声 | 观察到误触发：音箱自播“理想同学”即触发唤醒，需继续评估 |
| 断线恢复 | 通过。容器重建后 Client 自动重连；重启音箱后自启并重连 |
| 距离/音量 | 远距离或偏小声会漏检；1 米内正常音量可稳定触发 |

已知限制：音箱 DHCP 地址会变化（本次 `.246` → `.247`），需要路由器固定租约；4399 仅靠 Token 鉴权，未做来源 IP/MAC 限制；唤醒反馈为提示音而非语音；播放中唤醒、远场、闹钟/计时器/音乐/米家的逐项对照尚未执行。回退方式保持不变：`update.exe bulkcmd "     setenv boot_part boot1"` + `saveenv` 后重启，回到官方 1.94.13。

### 识别率排查与调参结论（2026-10-25）

用户反馈“离两三米慢叫”“离近快点叫”识别不好，因此对 KWS 链路做了离线复现与参数扫描。结论是：**当前配置已在该录音数据上达到上限，调参无法提升召回**，后续若要继续改善需要标注数据或换用更强的检测方案。

关键前提：客户端 `packages/client-rust/src/services/audio/record.rs` 把 `arecord` 的 S32_LE 用 `sample >> 8` 转成 i16（代码注释称 A113 PDM 数据位于低 24 位）。本机实测 `pcm.noop` 送出的 S32 数据低 16 位恒为 0，即实际是左对齐 16 位，所以 `>>8` 相当于 **+48 dB 增益再硬削顶**（约 1.6% 样点被削）。这是线上 KWS 真正听到的音频；按“正确”的 `>>16` 还原后电平低 48 dB，同一段 64 秒录音只能命中 1 次，而按 `>>8` 复现恰好命中线上日志记录的 4 次。排查早期“离线复现不上”的原因即在此。

已用生产等价音频（`>>8` 削顶 + 512 帧 Silero VAD 门控 + 静音 480 ms 后 reset）扫描过的组合，均未超过基线 4 次命中，部分更差：

| 变量 | 取值 | 结果 |
| --- | --- | --- |
| `keywords_score` / `keywords_threshold` | 1.0/2.0/3.0/4.0/6.0 × 0.05/0.1/0.2/0.25 | 1.0–4.0 全为 4 次；6.0 降到 1–2 次 |
| `max_active_paths` | 8 / 16 | 无差异 |
| VAD `threshold` | 0.05 / 0.10 / 0.20 | 0.05 与 0.20 都为 3 次，低于基线 |
| `min_silence_duration` | 160 / 480 ms | 160 ms 降到 1 次 |
| 预卷 / 起始 reset | 0 ms、200 ms、480 ms；VAD 起始时 reset | 2–4 次，均未超过基线 |
| 固定增益 | 16–1024 倍 | 64 与 256 同为 4 次，其余更差 |
| 模型替换 | `sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20`（拼音词表 `l ǐ x iǎng t óng x ué`） | 同一录音 3 次，劣于现有 cjkchar+bpe 模型 |

维护提示：客户端 `>>8` 的转换在这套 `asound.conf` 下承担了“自动增益 + 限幅”，是远场能识别的前提；若按直觉改成 `>>16` 会把召回打到 1/4，需重新验证后再动。上游客户端若更新该转换逻辑，也必须重测唤醒率。

清理：调参期间临时设置的 `LOGLEVEL=DEBUG`、容器内安装的 `click`、临时模型包与录音均已删除，容器已按默认 `INFO` 重建并验证真机唤醒（2026-10-25 11:02 触发成功）。

## 2026-10-25 晚间：KWS 停摆根因与远场命中率改进

上一节的结论是“调参无法提升召回”。当晚复测发现两处**代码层面**的问题，结论需要修正：命中率的上限不是参数，而是检测循环的实时性和 VAD 触发时机。

### 1. 静默停摆：轮询 sleep 让延迟无界累积

现象：唤醒突然全部失效，重启容器才恢复。

根因在 `core/services/audio/kws/__init__.py` 的检测循环：`MyStream.read()` 在积压不足一帧时返回空，旧代码空读后 `time.sleep(0.01)`。客户端按 90 ms 一块送音频，消费者每次空转都晚于生产者，延迟逐帧累积（约 50 秒涨到 2.4 秒），最后积压越来越大，唤醒要么极迟要么完全听不到。

修复（`core/services/audio/stream.py` + 上述检测循环）：

| 改动 | 说明 |
| --- | --- |
| `MyStream.wait_input()` | 用 `threading.Condition` 阻塞等待下一帧就绪，取代 `sleep` 轮询 |
| `MyStream.trim_input()` | 积压超过 1 秒就按整帧丢弃最旧数据，并 `SherpaOnnx.reset()`，保证延迟有界 |
| `pending_bytes` / 健康日志 | 每 30 秒记录正常帧、空读、语音帧、丢弃数、积压字节；断流 10 秒给 warning |

修复后实测：30 秒处理 936–937 帧（实时 937），积压稳定 1.8–2.6 KB（约 60–90 ms），丢弃 0。

### 2. 远场命中率：200 Hz 高通 + VAD 触发预卷

上一节的扫描用的是 0 dB 采集配置，且没有做预处理。用同一批 2–3 米远场样本（`far16_1..5.raw`，各 5 秒）复现后：

| 处理 | far1 | far2 | far3 | far4 | far5 |
| --- | --- | --- | --- | --- | --- |
| 基线（0 dB，无滤波） | MISS | MISS | MISS | MISS | MISS |
| **+200 Hz 一阶高通** | HIT | HIT | HIT | MISS | MISS |

同时扫描过 60–400 Hz、一到二阶：150 Hz 与 200 Hz 一阶最好（3/5），400 Hz 降到 1/5；`keywords_score` 0.5–3.5、`keywords_threshold` 0.05–0.2、`num_trailing_blanks` 0–2、`max_active_paths` 8/16 全部无差异——说明漏检不是阈值问题，而是低频噪声主导。近场正样本（TTS 播报“理想同学”）在所有配置下都命中，没有回归。

另一处不一致解释了“离线能识别、线上不唤醒”：Silero VAD 判到人声的起点比实际起点晚约 0.3 秒，KWS 只收到 VAD 触发之后的数据，唤醒词第一个音节会被整段丢掉。给定一段真人远场录音（`round3.raw`，45 秒）：

| 预卷 | 该录音命中数 |
| --- | --- |
| 0 ms | 2 |
| **160 / 320 / 480 ms** | **3** |

配置项（`config.py` 的 `kws` 段，原生模式不覆盖即用默认值）：

```python
"highpass_hz": 200,   # 送 VAD/KWS 前一阶高通，<=0 关闭
"preroll_ms": 320,    # VAD 触发时补送的预卷，0 关闭
```

代价：容器 CPU 从约 12% 升到约 17%（2 核机器可接受）。

### 3. 状态与限制

- 上述改动已在容器 `xiaoai-lixiang-wake` 生效（启动日志带 `高通:[200.0Hz], 预卷:[320ms]`）。因为镜像重新 build 受网络阻塞，本次用 `docker cp` 把文件写进容器后 `docker commit` 回 `xiaoai-native-wake:validation`，再 `docker compose --env-file ../device.env -f docker-compose.device.yml up -d --force-recreate` 重建；同时把 `LOGLEVEL` 恢复为 `INFO`。**这是镜像快照而非可复现构建**：下次能联网时仍需按 `Dockerfile` 正常 build，并从仓库源码核对容器内的 `core/services/audio/{stream.py,kws/__init__.py}`。
- 远场样本只有 5 条，3/5 不是验收数字；真人实时复测（5 声提示音 × 2 轮）尚未完成。
- 采集侧没有硬件增益控制可用（`amixer -c 0 scontrols` 只有播放侧 `mysoftvol`/`notifyvol` 与 PDM 参数），实测远场语音只占 16 位数据的约 7–8 位；上一节“`>>8` 是远场能识别的前提”仍然成立，不要改成 `>>16`。
