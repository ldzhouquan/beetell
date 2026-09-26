 # BeeTell（蜜语）系统设计文档

| 项 | 值 |
|----|----|
 | 版本 | v0.3 Draft（v0.2→v0.3 依据评审修订；v0.1 曾用名 WAH / Watch Agent Hub） |
| 日期 | 2026-09-18 |
| 状态 | 已完成对抗式评审并修订（v0.2→v0.3）；M0 开工基线 |
 | 命名 | BeeTell（中文名「蜜语」），取自欧洲民俗 "Telling the Bees"：家有大事，告于蜂群；蜂记得家中发生的一切 |
| 关联 | 《C·ONE 调研报告》《Watch App 可行性分析》《决策清单 D1–D5》；对抗式审查：docs/system-design-review.md（v0.3 修订依据） |

## 1. 背景与目标

### 1.1 背景
 C·ONE（亿嘉和，599/799 元档 AI 语音硬件）核心能力：①通知捕获 ②长按语音 ③双击录音+标记 ④RGB 灯带 ⑤.md 知识库 ⑥磁吸+30 天续航 ⑦App 面板/多模型 ⑧开发者生态。可行性分析结论：Watch App 无法复刻①⑥等硬件专属能力，但 ②语音指令 与 ③AI 录音纪要 可完整实现。本项目据此收敛：**只做②+③**；后端不绑定单一 Agent（OpenClaw 只是可选 adapter 之一），改为 BeeTell 网关（Agent Hub）+ 可插拔 AgentAdapter。

### 1.2 目标
- G1a 语音指令·回显：Watch 按住说话（PTT），松手后 asr.final 文本可见 ≤ 2s（局域网）。
- G1b 语音指令·回复：首句回复（首个 agent.delta → TTS 起播）≤ 4s；完整回复经 agent.delta 流式继续，不设完整回复时限（A1，v0.3 拆分）。
- G2 录音纪要：一键开/停会议录音，双击打标记；网关侧转写并生成 Markdown 纪要。**场景钉死（C1）：单人口述 / 通话 / 面对面 1 对 1**；多人远场混音不做承诺（说话人分离在非目标）。
- G3 Agent 可插拔：协议稳定；Codex/Hermes/通用 LLM API 均以 adapter 接入，增删不改客户端。
- G4 数据边界（诚实表述，B1）：音频转写、存储、凭据全部本地/家中；传输默认经 Tailscale 加密（D7）；Agent 推理按 BYO 可将**转写文本**送往云端 LLM——真·本地推理列非目标（§1.3）。

### 1.3 非目标（一期不做）
- 通知捕获：ANCS 仅向配对的蓝牙配件广播，Watch App 无法监听（可行性分析已证伪）。
- 实时字幕、说话人分离、知识库检索。
- 多租户 / 账号 / 计费 / App Store 上架（D1 自用：侧载或 TestFlight）。
- Android / Wear OS。
- 真·本地 Agent 推理：一期不做本地 LLM adapter；`codex --oss` / 本地 provider 仅作为后续低成本入口（E2）。

## 2. 决策记录（已拍板）

| # | 决策点 | 结论 | 依据与备注 |
|---|--------|------|-----------|
| D1 | 发布形态 | 自用，侧载 / TestFlight；无账号、计费、多租户 | 砍掉服务端账号体系 |
 | D2 | 模型凭据 | BYO：凭据只住网关；Watch/伴侣 App 仅持 btell token | 泄露面压到网关一台机器 |
 | D3 | ASR | 本地 faster-whisper large-v3 int8 起步；mlx-whisper 备选；whisper.cpp 进 M0 横评 | README 已核实 CPU/GPU 均可 8-bit 量化；v0.3：横评双口径（会议 RTF≤0.5 / PTT 首字延迟） |
| D4 | 伴侣 App | 最小版：扫码下发配置 + 纪要查看/分享；范围钉死 | 防范围蔓延 |
| D5 | 首个 Agent | **v0.3 改**：PTT 默认 llm-adapter（SSE 流式，撑 G1a/G1b）；codex-adapter 延后至 M2 承接复杂指令/长任务 | A1：codex 回合级输出与 4s 首响不兼容；codex exec 实测无逐 token 流 |

未拍板项一律按 §4/§9 的默认值执行，M0 结束前可改。

### 2.1 评审修订决策（D6–D10，v0.3 新增；依据 docs/system-design-review.md）

| # | 决策点 | 结论 | 依据与备注 |
|---|--------|------|-----------|
| D6 | 会议转写时机 | 增量转写：分片到达即转写入库，finalize 只做末片+合并润色 | 消解 A3 数值矛盾（60min×RTF0.5=30min > 15min） |
| D7 | 网络拓扑 | Tailscale 常开为默认：家内外同一 100.x baseURL，链路默认加密 | 收口未决#4；解决 IP 漂移（E1） |
| D8 | Web 设置页安全 | 一次性配对码；绝不渲染 config；doctor 输出脱敏；token 低权限化 | B2 |
| D9 | PTT 断线语义 | 断线=丢弃当前话语+触觉提示重说；仅会议走 REST seq 续传 | C3（取更简解，PTT 不做补传） |
| D10 | TTS 位置 | 一期设备端 AVSpeechSynthesizer；tts.chunk 帧预留，server_tts 默认 off | D3 二选一（低延迟、离线、零成本） |

## 3. 总体架构

### 3.1 拓扑

```
[Apple Watch]──Wi-Fi──┐                [Mac mini 4]
  PTT / 会议录音        │                 BeeTell 网关 (FastAPI, launchd 常驻)
 标记 / TTS 反馈       ├────局域网──────►  ├─ AHP v1（REST + WS）
 纪要查看             │  (Tailscale 常开) ├─ 纪要管线（模板+标记注入 → .md）
[iPhone 伴侣 App]────┘                   ├─ ASR worker（faster-whisper）
  扫码配对 / 纪要分享                         └─ AgentAdapter SPI
                                               ├─ codex-adapter（子进程）
                                               └─ llm-adapter（OpenAI/Anthropic SSE）
```

### 3.2 设计原则
1. Watch 只做采集与反馈：不做 ASR、不跑 Agent、不长期存储；崩溃后凭本地分片续传。
 2. 单一连接：Watch 永远只连 BeeTell 网关（单一 baseURL + btell token），不感知后端是哪个 Agent；baseURL 固定为 Tailscale 100.x 地址（D7），家内外不漂移。
3. 协议先于实现：AHP v1 是稳定契约；adapter 增减不改客户端与 ahp 层。
4. 能力降级显式化：通过 capabilities 协商，客户端对每个不支持的能力有确定的降级行为。
5. 全自研：不引入任何开源项目代码；只用开源库（FastAPI、faster-whisper、ffmpeg）与系统框架（AVFoundation、SwiftData 等）；同类开源项目仅作"对答案"参考。

### 3.3 模块职责边界

| 模块 | 职责 | 明确不做 |
|------|------|----------|
| Watch App | 采集音频/标记、连接管理、分片续传、TTS 反馈、纪要查看 | ASR、Agent 调用、长期存储 |
| 伴侣 App（iOS） | 扫码配置下发、纪要浏览/分享 | 采集、控制 Watch 录音、对话 UI |
 | BeeTell 网关 | AHP 协议、会话/会议状态机、ASR 调度、纪要生成、adapter 路由 | UI、账号、多租户 |
| AgentAdapter | 把统一对话/工具语义翻译到具体 Agent | 触碰音频与转写 |
| ASR worker | 音频→文本（分片/partial/final） | 理解语义 |

 ### 3.4 命名规范：`btell`（短形）与 `beetell`（全形）的分工
 实现层不统一拼写，按"键入频率 vs 品牌可见度"分两级使用，且任一标识符只允许一种拼写（禁止出现 `com.btell.*`、`beetell_token` 之类的混拼）：
 - **短形 `btell`**（高频键入、机器接口）：CLI（`btell` / `btell doctor`）、Python 包 `btell/`、配置目录 `~/.btell/`、token（`btell-token` / `auth{btell_token}` / `keychain://btell/*`）、环境变量前缀 `BTELL_`、logger 名。
 - **全形 `beetell` / BeeTell**（用户可见、品牌面、reverse-DNS）：仓库名、URI scheme `beetell://`、launchd label 与 Bundle ID（`com.beetell.hub` / `com.beetell.watch` / `com.beetell.companion`）、App 显示名、Swift Target 名；Swift 类型前缀 `Bee-`（如 `BeeSession`，遵循 Swift API 设计指南"少用缩写"）。
 - 先例：GitHub CLI=`gh` 但 scheme=`github://`；VS Code CLI=`code` 但 scheme=`vscode://`。短名用于"手"，全名用于"脸"。
## 4. AHP v1 协议（REST + WebSocket）

### 4.1 传输与认证
- REST：`http://<host>:<port>/v1/...`；WS：`ws://<host>:<port>/v1/ws`。v0.3（D7）：<host> 固定为网关 Tailscale 100.x 地址，家内外一致，链路默认加密。
 - REST 认证：`Authorization: Bearer <btell-token>`；WS 首帧必须为 `auth`。
 - 配对：伴侣 App 扫码获得 `beetell://<host>:<port>?t=<token>&n=<名称>`（§7），Watch 从伴侣 App 经 WatchConnectivity 拿到同一配置。

### 4.2 REST 端点

| 方法 / 路径 | 用途 |
|-------------|------|
| GET /v1/agents | 列出可用 adapter 及 capabilities |
| POST /v1/sessions | 创建 PTT 对话会话 → session_id；body 可选 `agent` 指定 adapter（D1 v0.3），缺省=llm-adapter |
| POST /v1/meetings | 创建会议（录音纪要任务）→ meeting_id |
| POST /v1/meetings/{id}/chunks | 上传音频分片（带 seq，幂等可重放；接受乱序，按 seq 重组——D4 v0.3） |
| POST /v1/meetings/{id}/finalize | 结束会议，触发纪要管线 |
| GET /v1/meetings | 会议列表（id/状态/时长/纪要就绪） |
| GET /v1/meetings/{id}/minutes | 获取 .md 纪要 |
| GET /healthz | 网关 / ASR / adapter 健康与版本 |

### 4.3 WS 帧（JSON 单对象，`type` 字段判别）

客户端 → 服务端：
- `auth{token}` — 建立会话
- `audio.start{session_id,fmt}` / `audio.chunk{seq,pcm_b64}` / `audio.end{}`
- `text{session_id,text}` — capabilities 无 audio_in 时的降级通道
- `mark{meeting_id,ts,label}` — 双击打标记
- `cancel{scope: ptt|meeting}` / `ping`

服务端 → 客户端：
- `asr.partial{text}` / `asr.final{text}`
- `agent.delta{text}` / `agent.tool{name,args}` / `agent.done{text,usage}`
- `meeting.state{meeting_id,state,progress}`
- `tts.chunk{audio_b64}`（预留，D10：server_tts 默认 off，一期设备端合成） / `error{code,message}` / `pong`

### 4.4 错误码
- 4001 auth 失败；4002 协议版本不匹配；4003 未知 session/meeting；4004 分片 seq 不连续（v0.3/D4：仅会议 finalize 时按缺口报错，上传侧接受乱序按 seq 重组，不做严格按序拒绝）
- 5001 ASR 失败；5002 Agent 超时或崩溃；5003 存储不可写

### 4.5 capabilities 协商与降级

| capability | 含义 | 不支持时客户端行为 |
|-----------|------|--------------------|
| streaming | agent.delta 流式输出 | 等待一次性 agent.done |
| tools | Agent 具备工具调用 | 不展示 tool 帧 |
| memory | 跨会话记忆 | 每会话独立上下文 |
| audio_in | Agent 直接收音频 | 先由网关转写为 text 再喂 Agent |
| long_task | 长任务队列（纪要） | finalize 同步等待，UI 显示进度 |
握手流程（补评审 D2，v0.3）：① WS `auth{token, v:1}` 帧内带协议版本，不符即 4002；② capabilities 经 `GET /v1/agents` 获取，`POST /v1/sessions` 所选 `agent` 与所需能力由网关在会话创建响应中逐项确认（支持/降级），客户端按确认结果选择 §4.5 降级路径。

### 4.6 音频格式约定
- PTT：PCM 16 kHz 单声道 s16le，约 200 ms/帧（≈6.4 KB），seq 单调递增；断点续传按 seq 对齐。
- 会议：AAC 16 kHz，30–60 s/片上传；网关用 ffmpeg 统一转 PCM 16k 再入 ASR 队列。

 ## 5. BeeTell 网关

### 5.1 技术栈与运行形态
 Python 3.12 + FastAPI + uvicorn，launchd 常驻（§8）。数据全部落 `~/.btell/`：`config.yaml`（权限 600）、`meetings/`、`logs/`、`db.sqlite`。

### 5.2 目录结构

```
 btell/
├── ahp/        # AHP v1：REST 路由、WS 会话、帧编解码、错误码
├── core/       # 状态机：session/meeting、ASR 调度、纪要管线、ffmpeg 封装
├── adapters/   # AgentAdapter SPI + codex-adapter + llm-adapter
└── store/      # sqlite、文件存储、config 加载
```

### 5.3 AgentAdapter SPI

```python
class AgentAdapter(Protocol):
    name: str
    capabilities: Capabilities   # streaming/tools/memory/audio_in/long_task
    async def start_session(self, ctx: SessionCtx) -> SessionHandle: ...
    async def send(self, h: SessionHandle, text: str) -> AsyncIterator[AgentEvent]: ...
    async def cancel(self, h: SessionHandle) -> None: ...
    async def health(self) -> Health: ...
```

事件统一为 `AgentEvent`（delta / tool / done / error），由 ahp 层映射为 WS 帧；新增 adapter 不改 ahp/core。

### 5.4 codex-adapter（v0.3：延后至 M2，承接复杂指令/长任务）
- 同机子进程包装 `codex` CLI：会话启动拉起、cancel 杀进程、崩溃/超时报 5002。
- 事件映射：CLI stdout（非交互 / JSONL）→ AgentEvent；映射表在 M0 用 `codex --help` 现场核实后定稿（§11.1）。
 - 版本锁定：config 记录 codex 版本号，`btell doctor` 冒烟（§8.4）防接口漂移。
- 延迟事实（A1）：`codex` 为自主 agent 循环，回合级输出、无逐 token 流，完整回复 10–60s——不参与 G1b 4s 承诺，仅服务长任务/复杂指令。

### 5.5 llm-adapter
OpenAI / Anthropic Chat API + SSE → delta 流；模型与凭据读 config（BYO，D2），支持多模型并存、按会话选用。**v0.3（D5）：PTT 默认路径**——SSE 首 token 延迟低且天然流式，撑起 G1a/G1b。

### 5.6 ASR worker
- 双队列：`ptt`（短音频、高优先级，服务 G1a/G1b）与 `meeting`（长音频批量，服务 G2）。两队列各持独立模型实例，互不抢占（A3：转写中的 30s 窗口被抢占=白算）。
- faster-whisper large-v3 int8 起步（D3）；worker 为常驻进程持有模型，避免每请求重复加载。会议用 large（质量优先），PTT 按 M0 横评可选小模型（延迟优先）。
- M0 横评双口径（A2，v0.3）：会议=10 分钟音频 RTF ≤ 0.5；PTT=3s 短音频（VAD 裁剪+小模型）转写 ≤ 1s。

### 5.7 纪要管线
输入：转写分段 + 标记 + 会话元数据。流程（D6，v0.3）：**会议分片到达即增量转写**（30–60s 片 → 文本段入库）→ finalize 只做末片转写 + 段合并 → 标记注入切分议题 → 模板填充 →（可选）LLM 润色 → 落盘 `.md`。输出契约：

```markdown
---
meeting_id: ...   # front-matter：日期/时长/参与的 agent
---
# 会议纪要
## 摘要
## 议题（按标记切分）
## 行动项（owner / deadline / 事项）
## 风险与待定
## 原文锚点（时间戳 → 原文）
```

 ### 5.8 配置 `~/.btell/config.yaml`（权限 600）
 `server{host,port}`、`auth{btell_token}`、`agents{codex{...}, llm{...}, server_tts: off}`、`asr{engine,model,device}`、`privacy{audio_retention}`；第三方凭据可写 Keychain 引用（`keychain://btell/openai`）。**凭据优先级（B4）：Keychain 引用可读 → 用之；不可读（如重启未登录）→ 回退 config 明文并告警**，`btell doctor` 第 5 项核查。

## 6. Watch App

### 6.1 页面（5 个）
1. 主面板：PTT 大按钮 + 会议开始/结束 + 连接状态
2. PTT 对话流：转写/回复/TTS 开关
3. 会议进行中：时长、分片数、打标记按钮
4. 纪要列表与查看
5. 设置：网关地址/token、诊断（ping / doctor 结果）

### 6.2 核心服务

| 服务 | 职责 |
|------|------|
| AudioEngineService | AVAudioEngine 两套 audio session：PTT=voiceChat（16k mono）、会议=measurement；后台模式 audio |
| AgentGateway | 唯一网络出口：REST+WS、重连退避、帧编解码 |
| SyncQueue | 分片持久化队列 + seq 续传；崩溃/断网后补传 |
| MarkerStore | 双击打标记（时间戳+label），SwiftData 本地，随分片上传 |
| TTSPlayer | 回复朗读：一期设备端 AVSpeechSynthesizer（D10）；收到 agent.delta 即分段起播，可关 |

### 6.3 后台与功耗纪律
- 会议模式：`WKBackgroundModes: audio` 常驻录音并分片上传；PTT 仅前台按住。
- 机型分级：S 系列=仅前台会议；S9+ 与 Ultra=允许后台会议（待 M0 Spike 实测，§11.1）。
- 电量纪律（C2）：会议中电量 < 20% 自动暂停录音并震动提示；回充后续录（新 meeting_id 段，finalize 合并）。M3 验收机型钉死 S9+/Ultra。
- 屏幕常亮只在会议页开启；非会议期不持有 audio session。

### 6.4 SwiftData 模型
 `BeeSession(id, agent, startedAt)`、`Marker(ts, label, synced)`、`TranscriptSeg(meetingId, ts0, ts1, text)`、`AgentMessage(role, text, ts)`、`MinutesRef(meetingId, localPath)`、`SyncTask(kind, payload, seq, retry)`。

## 7. iPhone 伴侣 App（最小版，D4）
范围钉死为两件事：配置下发、纪要阅读分享。
 - 扫码配对：网关 Web 设置页生成二维码，内容 `beetell://<host>:<port>?t=<token>&n=<名称>`；伴侣 App 扫码 → `GET /healthz` 校验 → 写入自身 Keychain → 经 WatchConnectivity 推给 Watch。QR 生成前须先通过一次性配对码验证（D8）。
- 纪要：列表 + Markdown 渲染 + 系统分享面板；不做编辑。
- 明确不做：音频采集、控制 Watch 录音、任何 Agent 对话 UI。

## 8. 部署与运维（Mac mini 4）
 - launchd：`RunAtLoad=true`、`KeepAlive=true`（plist label 建议 `com.beetell.hub`），崩溃自动拉起；Watch 连接失败先怀疑网关进程。
- 电源：`pmset` 设置接电不睡眠（`sleep 0`，需 sudo），保证 ASR/Agent 随时可用。
- 网络（D7，v0.3）：Tailscale 常开为默认——家内外 Watch 一律连网关的 100.x 地址，单一 baseURL 不漂移（收口未决#4）；家庭局域网直连为备选。
 - 备份：`~/.btell/` 整目录纳入 Time Machine。
- 网关自带一个极简 Web 设置页（安全边界见 D8）：仅凭一次性配对码进入；生成配对二维码、查看脱敏后的 doctor 结果；**绝不渲染 config 内容**。

 ### 8.4 btell doctor 自检（五项全绿才算就绪）
1. 端口可达（本机 `curl /healthz`）
2. ASR 模型加载成功（一次 1 秒空音频冒烟）
3. codex 冒烟（一次性调用，预期输出返回）
4. ffmpeg 存在且可转码
5. 凭据可读（Agent/API 凭据与 Keychain 引用实际可达，B4）

## 9. 安全与隐私
三层凭据：

| 层 | 内容 | 存放 |
|----|------|------|
 | Watch | 仅 btell token | Keychain |
 | 伴侣 App | beetell URI → btell token | Keychain |
| 网关 | Agent/API 凭据（BYO） | config 600 / macOS Keychain |

- 传输（v0.3/D7）：默认全程 Tailscale 加密（家内外同一 100.x 地址）；家庭局域网明文 HTTP 仅为备选——Bearer token 可被同网嗅探，guest Wi-Fi/合租环境务必走 Tailscale（B3）。不开公网入口。
- 音频留存：`privacy.audio_retention` = `off`（默认，转写完成即删原始音频）/ `keep_raw_7d` / `keep_all`。
- 录音告知：会议开始时 Watch 震动 + 顶栏红点常驻；合规责任由使用者自负。

## 10. 可靠性矩阵

| 故障 | 表现 | 恢复策略 |
|------|------|----------|
| Wi-Fi 抖动 | WS 断开 | 重连指数退避；会议=REST 按 seq 续传不丢；PTT=丢弃当前话语+触觉提示重说（D9） |
| Watch App 被杀 | 录音中断 | SyncQueue 持久化；重启后按 seq 补传（后台被杀风险见 §13） |
| 网关崩溃 | 全部请求失败 | launchd autorestart；meeting 状态机落盘，可 resume |
| codex 崩溃/超时 | 无 agent.done | cancel ≤3s 生效；报 5002；会话可重建 |
| ASR 变慢 | final 延迟 | PTT 队列优先；超 2s 先回 partial 回显，超 8s 展示降级提示 |
| 磁盘写满 | 写入失败 | 5003；默认 audio_retention=off 缓解 |

## 11. 测试与验收

### 11.1 M0 现场核实（开工即做，随身清单）
1. `codex --help`：非交互调用与 JSONL/exec 输出契约 → 决定 codex-adapter 映射表
2. ASR 横评：faster-whisper / mlx-whisper / whisper.cpp，同一段 10 分钟中文会议音频，RTF ≤ 0.5 准入；另测 PTT 口径（3s 短音频+VAD，首字延迟 ≤1s，A2）
3. 双指连点（打标记手势）所需 watchOS 版本
4. 后台录音存活实测：S9+/Ultra 灭屏连续录音时长

### 11.2 Spike 判定表

| Spike | 判定线 | 不过时的预案 |
|-------|--------|--------------|
| 后台存活 | 连续录音 ≥2h 不被杀 | 降级仅前台会议；或降低采样率/亮度 |
| PTT 端到端 | 松手→asr.final ≤2s（G1a）；松手→首句回复起播 ≤4s（G1b） | asr.final 先行回显；G1b 不过→换更小 ASR 模型/更低延迟模型；流式中断提示「完整回复稍后到达」 |
| ASR 横评 | 会议：10min 音频 RTF ≤0.5；PTT：3s 短音频转写 ≤1s（A2） | large-v3 → medium；或换 mlx-whisper / whisper.cpp |

### 11.3 里程碑验收标准
- M1：PTT 10 连测（局域网）：松手→asr.final ≤2s（G1a）；松手→首句回复起播 ≤4s（G1b）
- M2：连续 10 轮 PTT 不崩；cancel 后 ≤3s 停止输出
- M3：60 分钟会议（验收机型钉死 S9+/Ultra，C2）：会议期间增量转写不掉片；finalize（末片转写+合并润色）≤ 2min；纪要含全部标记锚点
 - M4：`btell doctor` 五项全绿；全新 Watch 扫码配对 ≤1 分钟

## 12. 里程碑计划（约 10 周）

| 里程碑 | 周期 | 内容 |
|--------|------|------|
| M0 | 第 1 周 | 骨架：网关 AHP 骨架 + Watch 端 stub + 伴侣最小版；完成 §11.1 四项核实 |
| M1 | 第 2–3 周 | PTT 闭环：Watch→网关→ASR→llm-adapter（SSE 流式）→设备端 TTS |
| M2 | 第 4–6 周 | 稳态：续传 / 重连 / cancel / capabilities 降级全覆盖；codex-adapter 接入（复杂指令/长任务） |
| M3 | 第 7–9 周 | 会议纪要全管线：分片上传、标记、finalize、.md、伴侣查看 |
| M4 | 第 10 周 | doctor / 备份 / Tailscale / 耗电收尾 |

## 13. 风险登记册

| 风险 | 概率/影响 | 缓解 |
|------|-----------|------|
| Codex CLI 接口漂移 | 高 / 高 | 版本锁定 + doctor 冒烟；SPI 隔离，只改 adapter |
| watchOS 后台录音被杀 | 中 / 高 | M0 Spike 先行；不过则降级前台会议 |
| ASR 中文准确率不足 | 中 / 中 | 横评定引擎；加领域词表后处理 |
| Mac mini 单点故障 | 低 / 中 | launchd 自愈 + Time Machine；Tailscale 可远程重启 |
| Codex/LLM 首 token 延迟不达标 vs G1b | 高 / 高 | PTT 主路径改 llm-adapter 流式（D5 v0.3）；M0 实测首 token 延迟，不过则降级纯 ASR 回显 |
| Watch 远场收音质量（多人会议） | 高 / 中 | G2 场景钉死单人/通话/1 对 1（C1）；说话人分离仍在非目标 |
| IP 漂移 / 出门地址切换 | 中 / 中 | Tailscale 常开、家内外同一 100.x 地址（D7）；mDNS 备选 |
| Keychain 重启未登录锁定 | 中 / 中 | doctor 第 5 项凭据可读自检（B4）；config 明文兜底 |
| 未认证 Web 设置页泄露凭据 | 高 / 高 | 一次性配对码 + 不渲染 config + 输出脱敏（D8） |
| 会议转写时机歧义致 M3 不可达 | — | 已消除：增量转写拍板（D6） |

## 14. 附录

### A. C·ONE 功能对照

| C·ONE 功能 | 本项目 |
|------------|--------|
| ①通知捕获（ANCS 硬件专属） | ❌ 不做（可行性已证伪） |
| ②长按语音 | ✅ PTT |
| ③双击录音+标记 | ✅ 会议打标记 |
| ④RGB 灯带 | ⚠️ 变体：Watch 触觉/界面指示 |
| ⑤.md 知识库 | ⚠️ 一期只出 .md 纪要，检索不做 |
| ⑥磁吸 + 30 天续航 | ❌ 硬件属性，无法复刻 |
| ⑦App 面板 / 多模型 | ✅ 伴侣 App + Agent 可插拔 |
| ⑧开发者生态 | ⚠️ AgentAdapter SPI 即最小时刻面 |

### B. 参考项目 license（仅"对答案"，不复制任何代码）
MIT：kuma-voice、Omi、vellum-assistant；Apache-2.0：Kai；无 license：hey-neo、voice-collector（不可复制代码）；闭源：Voicenotes、Just Press Record；NOASSERTION：watchGPT、Whisperer。

### C. 术语
 BeeTell=项目名（中文名「蜜语」；曾用名 WAH），取自欧洲民俗 "Telling the Bees"——家有大事，告于蜂群，蜂记得家中的一切；AHP=Agent Hub Protocol（BeeTell 网关的通信协议）；PTT=按住说话；BYO=自带凭据；RTF=实时率（处理时长/音频时长）；SPI=服务提供者接口。

### D. 未决事项（M0 收口）
1. codex 非交互 / JSONL 契约
2. ASR 横评结果与引擎选型
3. 双指连点所需 watchOS 版本
4. ~~Tailscale 是否常开~~ 已拍板：常开为默认（D7，v0.3）
