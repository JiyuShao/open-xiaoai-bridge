# 原生唤醒模式运行手册

## 当前状态

本地分支 `feature/native-wake` 已实现 `NATIVE_WAKE_ENABLE=1` 独立模式。默认新词是“你好管家”，命中后只调用 `SpeakerManager.wake_up(awake=True, silent=False)`。等原生反馈后再说指令。原厂词继续由设备处理。

已完成软件替身测试，未完成真实模型推理、完整 Bridge 构建、服务器部署或音箱验收。没有刷机或安装 Client。

## 部署前准备

记录日：2026-10-24，Asia/Shanghai。

| 项目 | 当前状态 |
| --- | --- |
| 音箱型号 | 用户照片已确认小米小爱音箱 Pro LX06 |
| 当前固件 | 用户确认 LX06 当前版本为 **1.94.13**；与上游列出的 LX06 补丁版本一致，尚未验证本机安装与恢复 |
| 硬件修订、分区及备用系统 | 待确认 |
| SSH / Client / 改造历史 | 用户确认未刷机、未开启 SSH、未安装 Client；尚未访问设备 |
| 部署目标 | 用户现有 Docker 服务器（不使用 PVE）；系统、架构、Docker/Compose 版本、资源和访问方式待确认 |
| 本地开发 | macOS，uv 可用；隔离测试 Python 3.12.13 |
| 本地 Docker | shell PATH 未检测到，未验证镜像构建 |
| KWS/VAD 模型 | 六个必需模型文件尚未准备 |
| Git | 独立 fork，本地分支 `feature/native-wake` |

设备端需从补丁固件与 SSH/Client 安装条件开始准备。下一步确认可恢复的原厂备份、备用系统与刷机工具，同时确认现有 Docker 服务器的部署信息。固件版本一致仅解决版本匹配的一项条件，不代表已具备刷机条件。

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

此命令先通过 uv 同步完整上游构建依赖，再由 `main.py` 检查配置与模型、原子生成词表、同步加载 KWS/VAD，最后启动 Bridge 并监听 Client；不自行安装音箱端 Client，也不刷固件。当前尚未执行此启动步骤。

容器需从本分支构建镜像，不能直接使用不含本地修改的上游 `latest`。镜像已包含原生配置，CMD 直接使用项目 Python 执行 `main.py`，与命令行共享准备逻辑。设 `CONFIG_PATH=/app/config.native-wake.py`、`NATIVE_WAKE_ENABLE=1`、`AUDIO_INPUT_ENABLE=1`、`OPEN_XIAOAI_TOKEN` 并关闭其余后端及 API；将模型目录挂载到 `/app/core/models`。本机没有 Docker，未验证镜像构建，尚未提供标记为已验收的部署编排。

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
- 软件测试使用声学运行时和设备传输替身，不代表真实模型或 Rust 服务生命周期已经验收。

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

离线检查正确报告缺少六个 KWS/VAD 模型文件与私有 Token。完整 Bridge 构建和 Docker 镜像尚未验证。

### 启动集成验证

任务 02 增加临时项目中的真实入口测试，仅替换外部声学运行时和 Rust 设备传输。覆盖系统 Python 不可用时选择项目解释器、大小写/空白开关、Dockerfile 声明的运行文件与 CMD、缺模型与无效配置、空编码不替换旧词表、模型/服务失败和 SIGTERM 清理。容器测试通过本地执行其文件与命令验证启动契约，不能替代镜像构建。

此前的系统 Python `hashlib.file_digest` 异常、容器开关解析差异和容器遗漏原生配置已修复；真实模型推理、镜像构建和设备验收仍待完成。

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
