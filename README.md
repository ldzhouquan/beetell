# BeeTell（蜜语）

基于 [`docs/system-design.md`](docs/system-design.md) v0.3 的自用语音网关 + watchOS PTT/会议采集 + 最小 iPhone 伴侣 App。当前交付是**软件实现与本机协议测试**；没有完成 Watch 真机性能、ASR 横评、真实 BYO 云模型、Tailscale 家内外、60 分钟会议或正式部署验收。进度见 [`docs/execution-checklist.md`](docs/execution-checklist.md)。

## 本机启动网关

Python ≥3.9；建议使用隔离环境：

```sh
uv venv .venv
uv pip install --python .venv/bin/python -e .
.venv/bin/btell init                 # 首次生成 ~/.btell/config.yaml，权限 600；不覆盖已有配置
.venv/bin/btell serve                # 默认仅监听 127.0.0.1:8765
# 另一个终端：curl http://127.0.0.1:8765/healthz
.venv/bin/btell doctor               # 未就绪项返回非零，不输出 token/key
```

`BTELL_HOME` 可将私有配置/数据库重定位到其他目录。`btell init` 生成的 token 不在本仓库；也可用 `BTELL_TOKEN`（≥32 字符）覆盖。测试用的临时数据应放在私有目录，**不要提交真实录音、token、API key 或完整私人转写**。

可选 ASR：`uv pip install --python .venv/bin/python -e '.[asr]'`；首次运行时 faster-whisper 可能下载模型，**没有模型/ffmpeg 时 ASR 请求会报 5001，不会返回模拟文本**。默认会议 `large-v3`、PTT `small` 两套独立实例，尚未经 10 分钟/3 秒样本横评。ffmpeg 需单独安装。网关在会议 AAC 到达时立即转 WAV/增量转写；`finalize` 检查 seq/`expected_chunks` 后合并纪要。默认 `audio_retention: off` 在转写完成后删除音频；`keep_raw_7d` 在启动或后续上传时清理超过七天的已转写片段；备份策略请自行设置。

需要连接设备时，在 Mac 上先安装并验证 Tailscale，再将私有 `~/.btell/config.yaml` 的 `server` 设置为实际可达的 Tailscale 地址（不要把示例地址当真实地址）：

```yaml
server:
  host: 100.64.1.2             # 替换为本机实际 Tailscale IPv4
  port: 8765
  public_base_url: http://100.64.1.2:8765
auth:
  btell_token: REPLACE_WITH_A_RANDOM_TOKEN_OF_AT_LEAST_32_CHARACTERS
asr:
  model: large-v3
  ptt_model: small
agents:
  llm:
    openai_model: gpt-4o-mini
    openai_key: keychain://btell/openai
    anthropic_model: claude-3-5-haiku-latest
    anthropic_key: keychain://btell/anthropic
  codex:
    version: '0.144.1'         # 示例：请以本机 codex --version 为准
privacy:
  audio_retention: off
```

BYO 凭据优先 `BTELL_OPENAI_KEY` / `BTELL_ANTHROPIC_KEY` 环境变量；也可用权限 600 的配置值或 `keychain://btell/openai|anthropic`（macOS `security find-generic-password -s btell -a openai -w`）。Keychain 不可读时仅使用显式配置的 `openai_key_fallback` / `anthropic_key_fallback`，并发出**不含密钥的告警**。云模型会收到转写文本，请仅用获准数据调用。Codex adapter 需要明确版本号；它不参与 PTT 4 秒目标。没有凭据时会报 5002。

网关拒绝绑定公网地址；设备配对二维码**只**接受 Tailscale `100.64.0.0/10` 地址。默认 loopback 可供本机测试，但不能生成给手机使用的二维码。运行 `btell pair-code`（一次性、5 分钟）后在网关 `/setup` 页面输入代码，验证后才显示 `beetell://` 二维码。请勿截图/转发二维码或启用公网端口。页面不输出 config；`GET /healthz` 只显示非敏感状态。iPhone 扫码并经 WatchConnectivity 下发 Keychain 配置。Apple 客户端工程与构建说明见 [`clients/README.md`](clients/README.md)。

### 协议摘要

REST Bearer：`/v1/agents`、`/v1/sessions`、`/v1/meetings`、`/v1/meetings/{id}/chunks?seq=N`（原始 AAC/m4a 请求体，≤2 MB）、`/v1/meetings/{id}/marks`、`/v1/meetings/{id}/finalize`（JSON `expected_chunks`）、`/v1/meetings/{id}/minutes`。WS `/v1/ws` 首帧 `auth{token,v:1}`，然后 `audio.start{session_id,fmt:"pcm16-16000-mono"}`、`audio.chunk{seq,pcm_b64}`、`audio.end` 或 `text`；另有 `cancel`、`mark`、`ping`。PTT 断线丢弃当前话语，会议分片可幂等重传/乱序接收；缺口在 finalize 返回 4004。会议无实时说话人分离，纪要按模板填充并保留原文锚点，不自动声称已生成可靠行动项。

## 可复现检查

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q btell
swiftc -parse clients/Shared/*.swift clients/Watch/*.swift clients/Phone/*.swift
swiftc -o /tmp/beetell-gateway-smoke clients/Shared/BeeGateway.swift clients/Tests/GatewaySmoke.swift
/tmp/beetell-gateway-smoke
```

测试使用注入式 ASR/Agent 和本地 SSE MockTransport，不调用真实云模型；Swift `-parse` 不等于 Apple SDK typecheck 或真机构建。完整 Xcode + XcodeGen + 签名与设备、Tailscale、合法音频样本及 BYO 凭据仍是 M0–M4 实测前置条件。`btell doctor` 当前 ASR 项只查依赖，不替代设计中模型实际加载冒烟；Codex 项是版本存在性检查，不替代真实 agent 回合冒烟。部署模板见 `deploy/`，**未自动加载 launchd、改 pmset 或修改 Time Machine**。
