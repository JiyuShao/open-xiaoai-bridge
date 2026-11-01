# 2026-11-01 低音量真机复测

## 2026-11-01 HA 完全服务模式部署

用户选择方案 3：Home Assistant 完全放开。Bridge 现在使用镜像
`xiaoai-ai:ha-unrestricted-20261101`，私有环境为 `HA_ENABLE=1`、
`HA_READ_ONLY=0`、`HA_ALLOW_ALL=1`；Hermes 与 Home Assistant 原生镜像未修改。
`/api/tools` 只读核验返回 17 个工具，包含 `ha_call_service`、实体/状态/历史/服务查询以及规则和定时工具；健康接口返回 `healthy`、`speaker_ready=true`，重启次数为 0，Bridge 仍连接 `xiaoai-ai_default` 与 `1panel-network`。

allow-all 的 `ha_call_service` 将任意已注册 Home Assistant `domain/service/data` 交给 HA，调用通过统一操作记录和请求 ID 去重；审计只保存数据键名与 SHA-256 摘要，不保存任意服务参数原文。规则和定时任务继续走本系统的提案、确认、版本和持久化流程。该模式的放开范围仅是 Home Assistant API，不包括服务器 shell、Docker、文件系统、音箱运维或 Hermes 管理接口。

部署前备份保存在服务器 `/mnt/data/xiaoai-native-wake/releases/ha-unrestricted-20261101/`，包括 Compose、私有环境和父/新镜像检查信息。曾做过一次低风险台灯开关验证，但用户随后要求停止设备测试；已立即执行关闭并回读最终状态 `off`，本轮不再执行任何 HA 设备操作。后续验收只保留代码、健康、工具清单和审计的只读检查。

用户已解除静默限制，并在音箱旁配合验收。生产容器为 `xiaoai-ai`，初始检查镜像为 `xiaoai-ai:device-readonly-20261101`，运行正常、重启次数为 0，健康接口返回 `speaker_ready=true`。

通过受控接口提交音量 6 的原生指令后，使用 Vivi 播报短句；用户确认“有声音，声音很小”。随后提交音量 10 的指令并再次播报，用户确认音量可以。两次音量指令均仅有受理回执，没有本轮设备数值回读，不能把目标值当作回读值。播报接口返回 202；声音实际到达音箱的证据来自用户反馈。首条播报日志首块耗时 445 ms，同一日志窗口无 ERROR 或 Traceback；该耗时不代表完整语音对话延迟。

真人测试中，用户确认对话期间灯持续亮，但助手播报“抱歉，我没有收到回复”，连续回复未通过。生产日志出现三次 `ClientConnectorDNSError`。用生产混合配置和独立临时会话调用真实 `OpenAIManager.chat_turn()`，复现 DNS 失败、空回复，断言失败；该探针不播放音频。

根因为运行中的 Bridge 仅连接 `xiaoai-ai_default`，Hermes 在 `1panel-network`，缺少 Compose 已声明的共享网络。补回 Bridge 的 `1panel-network` 连接及 `xiaoai-ai` 别名后，相同探针通过：DNS 正常、非空回复、无错误，单轮耗时 4228 ms。Hermes 反向请求 `http://xiaoai-ai:9092/api/health` 也成功，`speaker_ready=true`。修复没有重启两个服务或清空用户会话；Bridge 启动时间与重启次数未变。

服务器 Compose 中镜像引用从滞后的 `xiaoai-ai:led-session-20261101` 更新为当前实际运行的 `xiaoai-ai:device-readonly-20261101`，保留原有双网络声明。配置变更前备份在服务器 `/mnt/data/xiaoai-native-wake/releases/network-recovery-20261101/docker-compose.device.before.json`。以后重建应使用该 Compose，并检查实际容器仍连接两个网络；仅检查健康接口不能发现 Hermes 目标 DNS 不通。

网络修复后，用户按“豆包豆包，你好”及不重复唤醒词的“再打个招呼”复测，确认“两轮都正常回复”，真人唤醒及连续两轮回复通过。会话灯常亮已在故障轮次由用户确认。

### 方言阶段偏好修复

用户试听结果为“第一次是四川话，第二次恢复普通话”。仅提取真实默认会话的语音元数据，发现 `voice` 从 `{"explicit_dialect":"sichuan"}` 变成 `{}`，没有 `session_preference` 标记。旧实现不缓存省略范围字段的要求，故下一轮方言为空。用该参数序列在真实聊天入口及 TTS 记录出口建立回归，旧代码稳定失败；独立真实 Hermes 合成会话有时每轮重复方言，未稳定复现，不能用它取代故障序列。

修复遵循用户确认的“默认延续”：Hermes 回复中的有效声音参数省略范围字段时默认保留为当前 session 阶段偏好；显式 `session_preference=false` 只覆盖当句，空 `voice` 使用已存阶段偏好，新会话清除。共享 `VoiceDirective` 的默认行为及直接播报接口仍为单句。更新模型提示，说明单句覆盖与恢复普通话的参数要求。

隔离回归 58 项通过，覆盖方言跨轮、单次英语覆盖后恢复四川话、切回普通话、新会话清除、TTS 路由及混合/原生唤醒；测试中为流式播放夹具补齐 `tts_stream_play`。实际补丁镜像再次通过故障序列回归。生产于 2026-11-01 16:55:45（Asia/Shanghai）切换至 `xiaoai-ai:dialect-persistence-20261101`，镜像 ID 为 `sha256:a76c135c1d3c6e2f4426c394412bcb0cae7f92de1e48e2425fc365b84d95b8c6`，仅在原生产镜像上替换 `core/openai.py`、`core/services/tts/voice.py` 与 `config.mixed-wake.py`。本地此前未发布的推理命令拦截不在该补丁中。

切换前备份 Compose 与私有变量至服务器 `/mnt/data/xiaoai-native-wake/releases/dialect-persistence-20261101/`，将统一 `.env.services` 的 HA/常驻记录开关同步为当前容器值，确认渲染后环境无差异、数据挂载一致，再通过 Compose 重建。音箱已重连，部署后真实 Hermes 两轮均返回非空回复、方言 `sichuan` 且缓存存在；探针不额外播报。用户真人复测确认“两轮都保持四川话”，方言切换与跨轮延续听感通过。随后按“只这一次用英语打个招呼”及“再打个招呼”测试，用户确认“第一次英语，第二次恢复四川话”，单句覆盖后恢复阶段偏好通过。恢复普通话、情绪、远场和真人打断仍待验收。

### Vivi 情绪指令修复与试听

用户确认恢复普通话成功，但最初平静/开心两种要求听感区别不明显。只提取语音元数据发现模型可仅给出 `emotion`，没有 `context_text`。Python 与 Rust 的合成请求又都把 `context_texts` 放在 `req_params` 顶层；[当前官方 HTTP 协议](https://docs.volcengine.com/docs/doubao-voice/unidirectional-streaming-text-to-speech-http?lang=zh)将该字段列在 `additions` 内，本仓历史协议资料同样注明 `req_params.additions.context_texts`。

新增回归先复现：旧 Python 请求体的 `additions` 中缺少语气指令；只有情绪标签时 `VoiceDirective.context_texts()` 为空。修复 Python/Rust 构造器的参数层级；2.0 使用自然语言语气指令，单独的情绪标签生成通用语气指令，模型提供的具体指令优先；1.0 多情感音色保留 `audio_params.emotion`。Hermes prompt 补充具体语气描述合同，不新增角色、声音模型或音量设置。

66 项 Python 隔离回归与 3 项 Rust 单测通过，新镜像可加载扩展。复用固定工具链和依赖缓存，比较构建层与本地 Rust 源码/锁文件共 11 个文件，仅本次 `native/src/tts/doubao.rs` 不同。使用新扩展 `tts_stream_collect` 向真实 Vivi 请求平静与开心两种合成，均有 PCM 返回，首块分别 528/493 ms；该探针未播放，也不将字节数差异当作情绪质量证据。

生产已部署 `xiaoai-ai:emotion-instructions-20261101`，镜像 ID `sha256:bf2b1100afaffc867fd360a1316b3b13c7e8dd9abd7a72663ed75eb25e1edc19`。相对前一个方言补丁，仅替换语音指令解析、Python 豆包 payload、混合配置及 Rust 扩展。保持现有低音量，受控播报“第一段/第二段：很高兴见到你，今天又是美好的一天”，先平静、后开心；用户反馈“有一些区别，但比较轻微”，并选择保留自然细微风格。恢复普通话与自然细微情绪听感通过。

构建产物与切换前 Compose 保存在服务器 `/mnt/data/xiaoai-native-wake/releases/emotion-instructions-20261101/`，前一方言镜像仍可用于回退。此次切换没有修改 Hermes 镜像、会话或音箱固件。

### 播放中自定义唤醒的回声回滚

低音量真机测试发现：播放长段 Vivi 回复时让麦克风/KWS 保持开启，会把音箱自己播出的“豆包”误识别成新的“豆包豆包”；误识别发生后旧播报仍可能继续，不能作为可靠打断。该现象在临时 `xiaoai-ai:barge-in-20261101` 镜像中复现。

已撤回播放中保持自定义 KWS 的临时实现，生产于 2026-11-01 回滚到 `xiaoai-ai:emotion-instructions-20261101`。回滚前 Compose 备份在服务器 `/mnt/data/xiaoai-native-wake/releases/echo-safe-rollback-20261101/docker-compose.device.before.json`。隔离回归 `test_mixed_wake.py` 7/7、`test_native_wake.py` 12/12 通过；回滚后 Bridge 健康接口返回 `healthy`、`speaker_ready=true`，重启次数为 0，仍连接两个 Docker 网络。

当前安全边界是：Vivi TTS 与提示音期间关闭录音/KWS；用户讲话等待窗口再恢复 KWS。原生“小爱同学”事件仍可停止当前播放并进入新对话，自定义“豆包豆包”在播放中的打断不列为通过项。要支持后者，需要单独引入硬件/驱动级 AEC 或经过验证的回声抑制链路；在此之前不再保持麦克风裸开。

### API 直播放音回声修复

首次设备侧探针调用 `/api/tts/doubao` 时确认了遗漏路径：该接口的长文本中包含“豆包豆包”，播放期间产生 1 次 KWS 事件并启动第二段 TTS。原因是 API 直播放音没有沿用连续对话的 KWS 生命周期；这不是新的固件问题。

补丁让阻塞和后台 API TTS 都在整个实际播放生命周期内暂停 KWS，并在播放结束后恢复原状态；新增回归先验证播放中 KWS 已暂停，再验证结束后恢复。相关 API、混合唤醒和原生唤醒回归共 36 项通过。生产切换至 `xiaoai-ai:tts-kws-guard-20261101`，切换前 Compose 备份在服务器 `/mnt/data/xiaoai-native-wake/releases/tts-kws-guard-20261101/docker-compose.device.before.json`。

切换后再次播放包含“豆包豆包”的长文本：HTTP 200，完整 TTS 1 次，播放期间自唤醒事件 0 次，异常计数 0；容器健康接口仍返回 `speaker_ready=true`，重启次数为 0，双网络保持不变。该结果证明设备自播回声路径已关闭；用户主动在播放期间说自定义唤醒词的可靠打断仍需 AEC，继续按 ADR-0006 的边界处理。

### 播放中打断能力门

为避免以后误配置重新打开裸麦克风路径，混合配置新增 `wakeup.barge_in` 能力门，对应环境变量 `BARGE_IN_ENABLE` 与 `BARGE_IN_ECHO_REFERENCE`，默认均为关闭。只有两项同时为真，路由器才会接受播放中的自定义唤醒；缺少回声参考时返回 `echo_reference_unavailable` 并保持安全策略。能力门部署在 `xiaoai-ai:barge-capability-gate-20261101`，默认配置没有改变当前行为。

能力门相关混合/原生回归 20 项通过；部署后再次播放包含“豆包豆包”的低音量长文本，自唤醒事件 0 次、异常 0 次，健康接口保持正常。当前 LX06 未声明回声参考，因此尚未启用用户主动播放中打断。

## 2026-11-01 side chat 静默收尾（历史记录）

本轮只验证代码、票据和隔离自动化，不触发音箱播报、不执行真实 HA 写操作、不刷写设备。服务器使用 `xiaoai-native-wake:validation`，测试容器无网络、UID 65534、零 capabilities、只读根文件系统、无 Docker socket 和生产凭据。

完整套件在每个测试模块独立进程中运行，避免边界夹具重载 `core.*` 后污染后续模块：可自动化模块全部通过；4 个仅用于真实豆包服务的脚本在无凭据环境下明确跳过。第二个独立容器验证了聊天会话、未知操作记录、环境音频、ASR 队列和日报任务恢复，未知设备操作没有重放。产物保存在服务器 `/mnt/data/xiaoai-native-wake/side-chat-validation/artifacts/`，其中 `summary.json` 的 `success` 为 `true`，`recovery-result.json` 的 `stage` 为 `passed`。

当前生产状态仍以服务器私有环境文件为准：HA 控制已启用并受确认、白名单、回读和审计约束，常驻记录已启用；本轮没有修改生产状态。真人声学、方言/情绪听感和其他 HA 写场景仍需单独人工验收。

## 2026-10-31 自动验收（历史记录）

本轮使用 Goal 管理剩余开发与自动验收。用户授权取消静默，继续低音量播放；HA 写操作仅限实际卧室照明灯，测试结束必须关闭。原生 Hermes 直连方案继续有效，不新增受限 Agent、不禁用 Hermes 自身工具，不修改其镜像；生产常驻录音保持关闭。长期自动化、提醒和其他房间/设备的写操作未获本轮授权。

## 真实验收

| 项目 | 证据与结果 |
| --- | --- |
| Hermes 原生工具与多轮会话 | 既有原生 API、skill_view / skills_list / terminal 调用项目 skill，双轮测试在同一 transcript 中保存12条消息 |
| HA 实体范围 | 通过真实 area/device/entity registry 确认“卧室”；仅选 `yeelink.light.lamp4` 照明灯，排除风扇指示灯、除湿机屏幕 |
| Hermes → skill → Bridge → HA 写入 | 临时测试 Bridge 只准一个卧室台灯；真实开灯后回读 `on`，重复ID不重发、冲突ID被拒绝；finally和独立清理均回读 `off` |
| 文件语音全链路 | 真实 Vivi 合成输入，本地 SenseVoice 识别介绍意图，原生 Hermes 回复，再经真实 Rust/Vivi/音箱播放；操作回执202，约3754ms，记录可查询 |
| 设备播放与打断 | 只读观察到 aplay 持续消费PCM；打断请求约73ms返回，随后aplay消失；原长播报记录 unknown/RuntimeError（被打断），没有冒充完整播放 |
| 真实 KWS/VAD 文件路由 | Vivi合成“理想同学”→native、“豆包豆包”→openai、普通讲话→无触发，三项通过；不是真人远场声学证据 |
| 声音阶段偏好 | 同一测试 transcript 的两轮英语问候均为英文、voice.language=en，阶段偏好true；部署重启后的全新进程清空本地偏好缓存，仍从原生会话延续英语，新增2条消息，同一ID共8条；本轮未读取用户聊天正文 |
| 后台分析会话恢复 | 使用合成文字、独立测试记录目录，真实原生Hermes两次分析跨实例复用同一ID；生产记录开关仍0，没有读取麦克风 |
| TTS 格式与声音参数 | 沿用同代码版本的既有真实 Python/Rust MP3、PCM合成/解码，以及英语、四川话和情绪参数成功证据，见静默验收记录；语气与方言听感仍需人判断 |

生产HA保持只读，台灯写测试使用临时Bridge实例和只允许该实体turn_on/turn_off的传输层，未创建规则、提醒或开启记录。私人操作库与日志保存于服务器 `/data/auto-acceptance-20261031/`（Bridge容器内），目录700、测试脚本umask077。公开记录仅使用实体摘要 `0239c48e0b`；不保存家庭状态全集、密钥或真实录音。

音量控制的受理回执不作为生效证据：设备 `player_get_play_status` 回读音量6；`get_media_volume`仍返回25，两个接口语义不同，未把它们描述为同一数值。测试持续使用低音量，没有自动提高或恢复音量。

## 修复与回归

- HTTP工具请求ID此前没有一直传到HA执行记录，同ID重试会导致重复服务调用。已由HTTP入口回归复现2次写入，修复后在真实台灯上验证只写一次及参数冲突拒绝。
- HA `assumed_state=true` 的乐观状态此前会被标为 observed_state/completed；现明确保留该标记并报告unknown，不把推测状态当作实际设备确认。
- 后台分析此前只有记忆Key，没有续接ID；现于记录根目录私有 `hermes-session.json` 原子保存/恢复响应ID，支持压缩后的ID轮换；启动不创建生产记录，会话仅在实际分析调用时使用。
- 原生 Hermes 可能用HTTP200返回finish_reason=error；现拒绝将内部失败保存/播报为成功回复，错误正文不写日志。
- 最终语音回复提示明确要求JSON声音信封；仍兼容没有信封的普通文字回复。

服务器无网络隔离回归207项：204通过、3项既有需显式启用的OpenClaw实测跳过。两次独立容器验证会话、操作记录、常驻片段和日报任务恢复，无操作重放；最终产物 `/tmp/xiaoai-hermes-native-validation.EdePcd/artifacts`。新增缺陷回归先复现问题，再验证修复。

## 最终部署与收尾

生产 Bridge 已切换至 `xiaoai-ai:auto-acceptance-20261031-final`，2026-10-31 11:59:13（Asia/Shanghai）启动。依赖与全部Rust源码SHA256和已验证镜像一致，复用该镜像的依赖与Rust扩展；最终Python/config运行源码SHA256逐项匹配本地。服务器公开源码和文档同步到原source目录，未覆盖私有 `.env.services`。切换前的Compose和私有环境备份在本次release的 `rollback-final/`，权限保持私有。

Hermes继续运行 `1panel/hermes-agent:2026.5.16`，启动时间仍为2026-10-28 16:36:40；HA继续运行 `homeassistant/home-assistant:2026.9.4`，启动时间仍为2026-10-17 13:30:47，均未重启/替换。旧文字适配器容器和镜像不存在，原生skill可直接列出Bridge的HA只读工具。

重建Bridge后用全新进程读取既有合成测试DB，复用同一原生transcript并验证服务器消息计数增长；没有创建默认用户聊天DB。恢复证据是测试会话，不冒充真实家庭对话验收。健康检查200、音箱TCP重连、arecord运行、aplay停止；启动以来没有Traceback/ERROR，容器重启计数0。临时9095端口关闭，无本任务测试容器遗留。

收尾再次读取实际卧室台灯，确认 `off` 且不是assumed_state。生产HA仍只读、常驻记录仍关闭。未鉴权status请求返回401，已配置密钥与全部仓库文件在内存比对，未发现泄露，旧TTS认证变量已移除。

本轮真实服务/设备证据不扩展为全部场景已实测：其他HA写入、场景/自动化/提醒的变更仍以隔离Mock验证；常驻录音用合成输入验证，未开启生产记录。真人声学与主观听感保留人工验收。

## 延迟诊断（2026-10-31）

用户反馈“询问到回复十几秒”。对生产日志按阶段核对后，主要原因是混合配置没有声明 TTS 的 `audio_format` 与 `stream`，运行时落回上游默认的 **MP3 非流式**路径：必须先完整合成、下载、解码，再开始播放。修复前日志样本中，豆包合成耗时为 1.2 秒到 9.7 秒；长回复还会叠加完整播放时长。Hermes 原生接口用临时会话直测约 1.05–1.20 秒，流式 PCM TTS 无播放直测约 0.42–0.48 秒。

已在 `config.mixed-wake.py` 显式设置 `audio_format=pcm`、`stream=true`，并加入显式 VAD 参数；先由回归测试复现缺失配置，再通过测试。生产 Bridge 已切换到 `xiaoai-ai:auto-acceptance-20261031-latency1`，短句真实低音量播报日志显示 `first_chunk=380–503 ms`。这只消除了 TTS 等待整段音频的问题；如果某一轮仍然慢，需继续看该轮从 ASR 完成到 Hermes 返回的时间，工具调用或复杂问题可能使模型端耗时增加。

## 会话数量只读核查（本轮测试期间快照）

Hermes当时共363个session：cron317、api_server40、feishu3、weixin2、cli1。这是原生Hermes全局数据库，不能把总数等同于本项目聊天数。API会话最早创建于本项目部署之前。

明确识别出4个本轮测试session：能力诊断2条消息、双轮直连12条消息/4次工具调用、台灯测试8条消息/3次工具调用、文件音频测试最初2条消息；后三者有 `xiaoai-doubao` 专用测试前缀。后续声音偏好测试复用文件音频会话，合成后台分析另用独立测试会话。其余36个API会话来源未知，其中32个采用原生 `api-<指纹>` ID，4个采用其他ID；没有读取正文/标题来推断归属。全局有17个明确父子关系，但这4个已识别测试会话当时均无parent，不归类为压缩派生。


时间均为 Asia/Shanghai；表中最后消息时间只来自元数据聚合，不读取正文。能力诊断的最后消息时间未在首批聚合范围内，保留未取值。

| 用途 | 当时消息数 | 创建时间 | 最后消息时间 |
| --- | --- | --- | --- |
| 原生API能力诊断 | 2 | 2026-10-31 11:18:20 | 无 |
| 两轮原生直连测试 | 12 | 2026-10-31 11:18:46 | 2026-10-31 11:18:54 |
| 卧室灯自动测试 | 8 | 2026-10-31 11:36:39 | 2026-10-31 11:36:47 |
| 文件音频自动测试 | 2 | 2026-10-31 11:41:10 | 2026-10-31 11:41:12 |

`X-Hermes-Session-Key`是稳定记忆作用域；`X-Hermes-Session-Id`让原生API从数据库载入工具和消息历史，响应头返回实际ID。原生API收到Id后忽略客户端历史；没有Id时用系统提示与首条用户消息指纹确定ID，单独传Key不会续接短期上下文。

生产默认语音配置为 `agent:xiaoai-doubao:readonly`。核查时其配置路径 `/app/app/xiaoai/chat-sessions.sqlite3` 尚不存在，所以没有真实用户语音mapping可核对；不能声称已验证生产默认用户会话，也没有证据说其ID被重启或失败弄丢。真实会话续接证据来自明确标记的测试会话。常驻记录当时关闭，后台Id缺失是未来启用后的代码问题，不能据此解释当时的全部session。

核查全程只读ID、来源、创建/结束时间、消息计数和消息时间；未读取聊天正文、标题或密钥，未删除、合并、重置会话。会话表没有独立updated_at，不能伪造该字段，更新时刻用最后一条消息时间近似。

## 最后人工验收

仅保留需要人的项目：真人近场/远场唤醒、家庭噪声与回声下的识别、原厂唤醒词的实际打断体感、Vivi音色/语气/方言的主观听感。合成文件和设备进程证据均不代替这些项目。
