# BeeTell 执行 Checklist

> 配套计划：[执行计划](execution-plan.md)；基线：[`system-design.md`](system-design.md) v0.3。所有条目初始为未完成；完成时勾选，并在文末登记可复核证据。`[ ]` 不代表失败，只有实测与验收通过后才能改成 `[x]`。

## 本轮软件交付证据（不等于 M0–M4 阶段验收）

- [x] 网关可启动：`BTELL_HOME` 指向 session scratch 私有目录，执行 `btell init`、`btell serve`，`curl /healthz` 返回 `{"service":"beetell","version":1,"asr_available":false}`；`btell doctor` 返回端口/ffmpeg OK、ASR/凭据/Codex NOT READY。见根目录 [README](../README.md)。
- [x] REST/WS、SSE adapter、会议持久化与配对的本机注入式测试：`.venv/bin/python -m unittest discover -s tests -q`（14 tests OK）；`compileall` 通过。见 `tests/`。**注入式测试不代表真机、真实 ASR/LLM 指标通过。**
- [x] Apple 客户端源工程和本机共享层检查：`clients/project.yml` YAML 可解析，`swiftc -parse clients/Shared/*.swift clients/Watch/*.swift clients/Phone/*.swift` 通过；`swiftc` 编译 `clients/Shared/BeeGateway.swift` + `clients/Tests/GatewaySmoke.swift` 并运行，输出 `Shared gateway smoke passed`。完整 Xcode 未安装，未做 iOS/watchOS SDK 构建。
- [x] 代码中保留 D5–D10 的路径约束：默认 LLM SSE、增量转写、一次性配对、PTT 断线丢弃、设备端 TTS；在测试与源代码中可复核，**非体验/性能验收**。

**仍需资源与现场验证**：完整 Xcode/XcodeGen、iPhone 与 Watch S9+/Ultra、可用 Mac mini/Tailscale 家内外网络、BYO 云模型凭据、合法 3 秒 PTT/10 分钟会议及 60 分钟录音样本。当前没有运行真实 ASR 模型或云推理、2 小时后台 Spike、10 次 PTT 延迟、60 分钟会议、扫码 ≤1 分钟、launchd/备份恢复、低电量续段合并与 `btell doctor` 五项全绿。因此下列 M0–M4 原条目/门槛保持 `[ ]`；记录软件结果不能替代门槛证据。

## M0｜第 1 周：可行性 + 契约 + 骨架

- [ ] **M0-01** 准备 Mac mini、iPhone、S9+/Ultra、签名、Tailscale、BYO 模型测试凭据与合法样本；确保密钥/录音不入库。证据：设备/版本与样本清单（不含私密数据）。
- [ ] **M0-02** 实测 `codex --help` / 非交互 JSONL 事件，记录版本、成功/失败事件与 adapter 映射，不假定逐 token 流。证据：脱敏调用和事件表。
- [ ] **M0-03** 用同一 10 分钟中文样本横评 faster-whisper、mlx-whisper、whisper.cpp；会议 RTF ≤ 0.5，记录硬件/模型/资源开销。证据：测量表与选型理由。
- [ ] **M0-04** 3 秒中文 PTT + VAD/小模型分别测转写 ≤ 1 秒，确定 PTT 方案；与会议 worker 分离，核对内存与并行可行性。证据：逐次耗时与选型。
- [ ] **M0-05** 真机核实双指连点打标记所需 watchOS 版本/可用 API，失败时定替代交互。证据：设备、系统版本、手势测试记录。
- [ ] **M0-06** S9+/Ultra 灭屏后台录音连续 ≥ 2 小时 Spike，记录断录/电量；失败时明确 M3 验收影响和前台降级条件。证据：时长日志。
- [ ] **M0-07** 在家和外部网络实测 Watch 访问网关固定 Tailscale `100.x` baseURL；不可达则定受保护替代拓扑并修订 D7，禁止静默改用公网明文。证据：连通性/网络记录。
- [ ] **M0-08** 冻结 AHP v1 契约：REST/WS 帧、`auth{token,v:1}`、会话 adapter/能力确认、错误码、PCM/AAC 格式；确认 D9 仅会议续传并勘误设计中的泛化描述。证据：协议示例与契约测试。
- [ ] **M0-09** 建立网关/ASR worker/adapter SPI/数据层骨架、Watch 采集 stub、iPhone 配置 stub；跑通健康检查与最小鉴权，不暴露配置或凭据。证据：构建/冒烟测试。
- [ ] **M0-GATE** 汇总四项 §11.1 核实与网络 Spike；关键指标达标，或完成设计/验收基线修订并按新基线复测达标，才可勾选。保留选型、失败记录和后续决策；不以 stub 冒充功能验收。

## M1｜第 2–3 周：PTT 实时闭环

- [ ] **M1-01** 实现 `POST /v1/sessions`、WS 鉴权/版本、PTT PCM 16 kHz s16le 采集与 `audio.start/chunk/end`；测试未授权与错误版本。
- [ ] **M1-02** 常驻 PTT ASR worker，输出 `asr.partial/final` 并在 Watch 回显；与会议工作队列隔离。
- [ ] **M1-03** 默认 `llm-adapter` 用至少一个 BYO 提供商 SSE 输出 `agent.delta/done`；凭据只在网关侧，错误有可辨识反馈。
- [ ] **M1-04** Watch 端 AVSpeechSynthesizer 分段朗读首句、可关闭；一期不依赖服务端 `tts.chunk`。
- [ ] **M1-05** 记录每轮松手、`asr.final` 可见、首个 `agent.delta`、TTS 起播时间戳；云模型凭据、网络环境和测试音频条件留脱敏记录。
- [ ] **M1-GATE** 局域网场景连续 **10 次** PTT，逐次满足回显 ≤ 2 秒、首句起播 ≤ 4 秒；不能用 ASR 文本替代首句回复，保存逐次证据。

## M2｜第 4–6 周：可靠性 + 可插拔

- [ ] **M2-01** `GET /v1/agents` 能列出能力；创建会话显式选 adapter 并确认支持/降级，客户端按 `streaming/tools/memory/audio_in/long_task` 的约定处理。证据：契约测试。
- [ ] **M2-02** 接入第二类设计内 LLM 提供商与 `codex-adapter`，固定 Codex 版本、映射回合事件，替换 adapter 时不修改 AHP/客户端；Codex 不参与 G1b 指标。
- [ ] **M2-03** 完成取消传播、Agent 崩溃/超时与 5002 映射；`cancel` 后 ≤ 3 秒停止输出。证据：计时与故障注入。
- [ ] **M2-04** Watch 连接指数退避；PTT 断线丢弃当次话语并触觉提示重说，不做 PTT `seq` 补传。证据：中途断线测试。
- [ ] **M2-05** 会议 REST 上传基础按 `seq` 幂等接收重传和乱序，`finalize` 遇缺口报 4004；设备/网关重启后可恢复尚未确认的会议分片。证据：断网/乱序/重放/缺口测试。
- [ ] **M2-06** 覆盖认证、协议版本、未知会话/会议、ASR 失败、存储不可写的 4001–4003、5001、5003 路径；Web/日志不泄露 token、API key 或 config。
- [ ] **M2-GATE** 连续 **10 轮** PTT 不崩、取消 ≤ 3 秒，M1 延迟回归通过；能力降级与重连/上传契约测试通过。

## M3｜第 7–9 周：会议纪要管线

- [ ] **M3-01** Watch 开始/停止会议录音，AAC 16 kHz 按 30–60 秒分片；持久化 `seq` 队列、重试与恢复；录音期间明确红点/震动告知。
- [ ] **M3-02** Watch 打标记并传 `meeting_id/ts/label`；S9+/Ultra 后台录音、屏幕与电量策略实机可用；低于 20% 暂停并提示。
- [ ] **M3-03** 分片抵达后 ffmpeg 转 PCM、会议 worker 增量转写并持久化；PTT 同时发生时两类工作互不打断。证据：并行录音/对话日志。
- [ ] **M3-04** `finalize` 仅处理尾片、缺口检查、按时间合并分段及标记；重复提交不重复纪要；产出规定章节与原文时间锚点的 Markdown。
- [ ] **M3-05** iPhone 伴侣 App 完成最小范围：配置下发、纪要列表/阅读/系统分享；不增加录音、Watch 远程控制或对话 UI。
- [ ] **M3-06** 验证断网后续传、乱序/重复上传、Watch 被杀后恢复、低电量暂停与回充后新 `meeting_id` 段合并；无丢片/重复文字。证据：故障注入记录。
- [ ] **M3-GATE** S9+/Ultra 录制 **60 分钟**限定场景会议，转写全程增量且不掉片，`finalize` ≤ **2 分钟**，纪要包含全部标记锚点；提供分片数量、转写时间线与产物脱敏样例。

## M4｜第 10 周：配对、部署、运维

- [ ] **M4-01** Web 设置页仅一次性配对码验证后生成含 token 的 QR；不得渲染 config，doctor 输出脱敏，token 仅具客户端所需权限。证据：未授权/越权/泄露测试。
- [ ] **M4-02** iPhone 扫码后校验 `/healthz`，写 Keychain，通过 WatchConnectivity 向 Watch 下发配置；新 Watch 扫码配对 ≤ **1 分钟**。证据：实机录像/计时。
- [ ] **M4-03** `btell doctor` 实际核查端口、ASR 模型加载、Codex 冒烟、ffmpeg 转码、凭据可读；Keychain 不可读时按设计显式告警，仅已配置的明文兜底可用（config 权限 600）。证据：五项结果与失败模拟。
- [ ] **M4-04** launchd 自启/崩溃重启、接电防睡眠设置（需授权）、`~/.btell/` 备份与恢复演练；默认音频转写后删除，检查 `keep_raw_7d/keep_all` 配置行为。证据：重启/恢复记录。
- [ ] **M4-05** 验证家内外走同一受保护 baseURL、不开放公网端口；对明文局域网备选明确安全提示。证据：两种网络下的访问与暴露面检查。
- [ ] **M4-GATE** doctor **五项全绿**、新 Watch 配对 ≤ 1 分钟、M1–M3 核心回归通过；交付自用安装、故障排查与隐私边界说明。

## 验收记录

逐项完成时在此补记录或链接（日志务必脱敏；不要提交原始录音、真实 token 和 API key）：

| 条目 ID | 日期 / 环境与设备 | 测试样本 / 次数 / 指标 | 结果与证据链接 | 遗留问题或变更 |
|---|---|---|---|---|
| 本机软件（非 M 阶段门槛） | 2026-09-26 / macOS 26.1 arm64、Python 3.14 venv、Swift CLI | 14 个注入式 Python 测试；Swift 共享层烟测 1 次 | [tests/](../tests/)、[GatewaySmoke.swift](../clients/Tests/GatewaySmoke.swift)、[README](../README.md)；均通过 | 无完整 Xcode/真机、真实模型/凭据/网络，M0–M4-GATE 均未完成 |
| M0–M4-GATE | 待现场验证 | 见各阶段指标 | 未通过验收（无实际测试证据） | 真机、Tailscale、凭据、样本、部署环境仍需准备 |

> 门槛不达标时保留 `[ ]`，登记复测或设计变更；降低范围/调整验收标准须先更新 [`system-design.md`](system-design.md) 和[执行计划](execution-plan.md)，不能直接把原门槛勾成已通过。
