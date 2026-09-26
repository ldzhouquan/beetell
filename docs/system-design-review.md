> **归档说明**：本文档为对 `docs/system-design.md` v0.2（326 行，2026-09-18）的对抗式审查存档。评审方独立完成，并用本机 codex CLI 0.142.2 实测了 `codex exec --json` 的事件契约（thread.started / item.completed / turn.failed 等，未确认逐 token 流）。

> 采纳与修订结果见 `system-design.md` v0.3：§2.1 评审修订决策（D6–D10）及各章对应修改。

---

# BeeTell 系统设计 v0.2 对抗式审查

**审查方法**：以"目标可达成性 + 证据充分性 + 内部一致性"三条线压力测试设计；能实测的用本机验证（codex exec），无法实测的标注为"需 M0 核实"或"证据缺失"。关联文档（C·ONE 调研报告、可行性分析、决策清单 D1–D5）不在本工作区内，无法交叉核对原始证据，已单独列出。

## A. 目标与选型的矛盾（最高优先级，会直接毁掉验收）

### A1. G1「首响应 ≤4s」与首发 codex-adapter 根本冲突
- 证据：G1 定义"松手后端到端返回回复 ≤4s"（L17、L278），M1 验收"PTT 10 连测端到端 ≤4s"。但 D5 首发 agent 是 codex-adapter（子进程包装 `codex` CLI）。codex 是**自主 agent 循环**（推理→可能多步工具调用→完整回合），非交互 `codex exec` 产出的是回合级结果，首 token 秒级、完整回复 10–60s 是常态。把 PTT 语音指令路由给 codex，"4s 返回回复"在数学上不成立。
- 我在本机实测 `codex exec --json`：事件流是 `thread.started / item.completed / turn.started / error / turn.failed` 这类回合级事件，**未见逐 token delta**；且 0.142.2 直接被其配置模型（gpt-6-astra）要求"需升级新版 CLI"拒绝——**文档风险登记册里"高/高"的"Codex CLI 接口漂移"在评审现场就已经兑现了**，这不是假设，是现状。
- 文档自己的 Spike 判定表留了退路："先出 asr.final，文案提示完整回复稍后到达"（L273）——这等于**把 G1 的"首回复"偷偷改写成"ASR 回显"**。G1 现在不可证伪：写的是端到端回复，兜底是 ASR 文本。两个口径必须选一个。
- 建议：G1 拆成两个可测量指标——`TTF_asr ≤4s`（ASR 文本可见）+ `TTF_agent`（流式首 token 另设预算）；或 G1 默认走 `llm-adapter`（OpenAI/Anthropic SSE，TTF ~1–2s，天然流式），codex 只服务长任务/复杂指令；或明确"首响应"= asr.final + 本地确认音。

### A2. 4s 预算与 Whisper 的 30s 窗口计算矛盾
- 证据：Whisper 按 30s 窗口处理，**3s 的 PTT 音频也要过一整段 30s 窗口**。faster-whisper（CTranslate2 在 Apple Silicon 上只有 CPU 后端）large-v3 int8 的 RTF 约 0.3–0.7 → 一段窗口 9–21s，直接破 4s。只有 mlx-whisper / whisper.cpp（Metal/ANE）RTF 0.05–0.15 → 1.5–4.5s，仍吃满预算且没算 agent 与 TTS。
- 文档的 ASR 横评准入线 **RTF≤0.5 只保证"能实时转"，与 PTT 4s 完全无关**。且文档无 VAD/静音裁剪与短音频小模型方案。
- 建议：单独设 PTT 的 TTF 验收线；PTT 用小模型 + VAD 切段，会议才用 large；M0 横评的准入线应区分"会议实时性"与"PTT 首字延迟"两个指标。

### A3. RTF≤0.5 与 M3「finalize ≤15min」数值矛盾
- 证据：M3 验收"60 分钟会议 finalize ≤ min(60/4, 15) = 15min"（L279）。若转写在 finalize 时集中做：60min 音频 × RTF 0.5 = **30min 计算 > 15min 预算**。
- 只有"**会议期间增量转写**（分片到即转）"才能满足 M3。但 §5.6/§5.7 没写清 meeting 转写是边录边转还是 finalize 集中转。这是验收定义的空洞。
- 顺带：若边录边转，RTF 需 ≤1.0 才跟得上实时（0.5 只是缓冲）；且 PTT 与会议抢同一个常驻 worker/模型时，"双队列优先级"的抢占语义未定义（转写中的 30s 窗口被抢占 = 白算）。

## B. 安全与隐私

### B1. G4「数据不出家门」表述与 BYO 云端 LLM 矛盾
- 转写确实本地；但 codex/llm-adapter 会把**转写文本发到 OpenAI/Anthropic 云端**。"转写与 Agent 调用默认走家中 Mac mini"（L20）字面成立（计算在本机），但隐私语义上文本出网了。对自用可接受，但 G4 应诚实改写为"**转写本地、局域网默认、凭据本地**"；若要真·不出门，需加本地模型 adapter（llama.cpp/MLX），当前不在范围。

### B2. 未认证的 Web 设置页 = 局域网凭据泄露面
- §8 说 Web 设置页"生成配对二维码、查看 doctor 结果"（L228），**未提任何认证**。LAN 上任何人打开该页即可：拿到含 btell token 的二维码 → 调用网关（可烧 BYO API 配额=真金白银）、看 doctor 泄露模型/配置细节。若页面还渲染 config，则直接暴露 API key。
- 建议：首次设置用一次性配对口令/挑战-应答；页面绝不渲染 config 内容；token 低权限化（只能建会话/传音频，不能读配置）。

### B3. 二维码内嵌 token + 局域网明文
- 明文 HTTP + Bearer token 可被同网嗅探；出门 Tailscale 加密 OK。自用可接受，但家庭 guest Wi-Fi/合租场景需在安全说明里点明。低风险，提一句即可。

### B4. launchd 常驻 + Keychain 引用的重启陷阱
- 第三方凭据可写 Keychain 引用（L188、L243）。Mac mini 重启后**用户未登录前 login Keychain 是锁定的**，网关读不到 → 静默失败（ASR 本地无碍，但 Agent 全挂）。
- 文档"config 600 明文 vs Keychain 引用"两态并存，需定义优先级与 `btell doctor` 检查项（如"凭据可读"作为第 5 项自检）。

## C. 产品价值与能力可行性

### C1. Watch 麦克风位置 vs G2「会议纪要」的核心价值风险
- Watch 戴手腕，多人会议时麦克风离发言者远、且**明确不做说话人分离**（L24）→ 转写大概率是混乱的混合文本。C·ONE 是近口/衣领夹戴设备，Watch 无法复刻收音条件。这是 G2 的产品级风险，风险登记册里没有。
- 建议：把 G2 使用场景钉死为"单人/通话/面对面 1 对 1"，或在可行性上明示此局限；否则 M3"纪要含全部标记锚点"验收出来的可能是一堆垃圾文本锚点，指标过了、价值没有。

### C2. M3 的 60min 会议与 §6.3 机型分级不可兼得
- §6.3：S 系列仅前台会议（屏幕常亮）；S9+/Ultra 允许后台会议但依赖 M0 Spike（≥2h 未被杀，**尚未验证**）。60min 会议在 S 系 = 亮屏 1 小时 → Watch 电池中途耗尽 → 后半程录音丢失（SyncQueue 只兜底 App 被杀/断网，**不兜底电池没电**）。
- 建议：写明 M3 在哪种机型验收；增加"电量阈值暂停录音 + 断点续录"或明确接受 S 系不支持 60min 会议。

### C3. PTT 的断连续传路径未定义
- §10 说"分片已落盘，seq 续传不丢数据"（L253），但 PTT 走 WS 实时帧、会议走 REST chunks。WS 断开时 PTT 半途音频如何补传——重新 `audio.start` 还是转 REST 补？SyncQueue 是否也承载 PTT 分片？文档含糊，需明确。

## D. 协议 / API 契约问题

### D1. 会话创建无法选择 agent/模型
- REST 表 `POST /v1/sessions`（L89）无 agent_id/模型参数，§5.5 却称 llm"按会话选用"。客户端拿到 `GET /v1/agents` 的 capabilities 后，如何指定路由到哪个 adapter？补上会话级 agent 选择与逐会话 capability 确认。

### D2. capabilities 协商缺"握手流程"
- 有 4002 版本错误码（L113），但无版本协商字段（header/帧内 version？）；降级行为表齐全（§4.5），但客户端"如何请求/确认 capability"的协议步骤缺失。

### D3. `tts.chunk` 与设备端 AVSpeechSynthesizer 矛盾
- WS 帧表含服务端→客户端 `tts.chunk{audio_b64}`（L110），§6.2 的 TTSPlayer 却是设备端 AVSpeechSynthesizer（L207）。**两处不一致**，必须二选一：设备端合成（免费、离线、低延迟）或服务端合成（可换更高质量语音、但加延迟）。

### D4. 服务端要求 seq 连续（4004）与 Wi-Fi 抖动下的乱序冲突
- 客户端乱序上传即被 4004 拒。Wi-Fi 抖动下乱序是常态，严格按序重试易造成重试风暴/死锁。建议服务端接受乱序按 seq 重组、缺口延迟到 finalize 统一报错。

## E. 运维 / 网络

### E1. IP 漂移 vs 「单一 baseURL」
- 配对 QR 内嵌 host:port；家庭 DHCP 换 IP → 配置失效。出门走 Tailscale 时是 100.x 地址，与 §3.2"Watch 永远只连单一 baseURL"（L57）冲突。建议：固定走 Tailscale 地址（在家也走），或 mDNS（`beetell.local`）+ 静态 DHCP 保留。正好呼应未决项 #4"Tailscale 是否常开"。

### E2. 小项
- `pmset` 改电源策略在 Apple Silicon 需 sudo，且"接电不睡眠"影响整机功耗；`codex --oss`/本地 provider 选项可作为真正本地化的低成本入口（实测 `codex exec` 已支持 `--oss`/`--local-provider`）。

## F. 设计优点（应保留）
- 范围收敛极克制：只做②+③、D1–D5 钉死、非目标明确（L22–26）。
- capabilities 降级显式化（§4.5）是好的协议设计。
- 分片幂等 + seq 续传、meeting.state 进度、launchd 自愈、doctor 四项自检——可靠性闭环扎实。
- M0 现场核实清单 + Spike 判定表 + 预案（§11）工程上很成熟，是全文档最亮的部分。

## G. 风险登记册缺项（建议补录）
| 缺失风险 | 依据 |
|---|---|
| codex 首 token / 流式延迟不达标 vs G1 | 只列了"接口漂移"，没列"延迟不达标"——**这是全项目最大风险** |
| Watch 麦克风收音质量 | C1 |
| IP 漂移 / 出门地址切换 | E1 |
| Keychain 重启锁定 | B4 |
| 未认证 Web 设置页 | B2 |
| 会议"增量 vs 集中转写"未定义导致的 M3 验收歧义 | A3 |

## 结论
设计**方向正确、工程纪律好**，但有**两个必须在 M0 前拍板的结构性问题**：① G1 的 4s 指标与首发 codex-adapter 不可共存——要么换 llm-adapter 撑 G1、codex 转做长任务，要么把 G1 拆成 `TTF_asr` + `TTF_agent` 两个可测量目标；② 会议转写时机（增量 vs 集中）未定义，直接导致 M3 数值上不可能与 RTF≤0.5 同时满足。其余为契约细节与安全补强项。另外建议：把关联的三份决策/调研文档纳入仓库，否则评审无法核对"证伪"证据（如 §1.3 ANCS、D3 README 核实）。

需要的话，我可以把以上整理成 `docs/system-design-review.md` 写入仓库，或直接据此把 §1.2/§4/§5.6/§5.7/§13 的对应条目改成可拍板的修订建议。要哪个？