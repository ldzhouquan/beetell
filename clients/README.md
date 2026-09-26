# BeeTell Apple clients（源工程，尚未完成 Apple SDK 构建）

`project.yml` 是 XcodeGen 工程定义，包含 BeeTellWatch（watchOS 10+）与 BeeTellCompanion（iOS 17+）两个 target。需要完整 Xcode、对应 SDK、XcodeGen、开发者签名与已配对 iPhone/Watch。进入 `clients/` 运行 `xcodegen generate --spec project.yml`，在生成的 `BeeTell.xcodeproj` 中为两个 target 选择 Team 后在真机运行；生成工程及配置不要填入或提交凭据。本机只有 Command Line Tools，`xcodebuild` 无法使用；`swiftc -parse` 只检语法，不证明 Apple SDK 类型、签名、后台录音或设备行为通过。

网关 `btell pair-code` 验证后，`/setup` 生成 `beetell://100.x.x.x:PORT?t=TOKEN&n=BeeTell` 二维码。iPhone 扫码校验 `/healthz`，将配置存到本机 Keychain 并用 WatchConnectivity application context 下发；Watch 重新校验并写入自己的 Keychain。客户端拒绝非私有地址的 QR，网关只签发 Tailscale `100.64.0.0/10` QR；不得通过公开网络暴露明文 HTTP 或分享二维码。iOS/watchOS App Transport Security 配置必须在真机和目标拓扑下复核。

Watch PTT 采集 16 kHz 单声道 s16le（约 200 ms/帧），WS 先发送文本 JSON `auth{token,v:1}`，然后 `audio.start{session_id,fmt:"pcm16-16000-mono"}`、顺序 `audio.chunk{seq,pcm_b64}` 与 `audio.end`；断线立即丢弃话语并震动提示重说，不做 PTT 补传。回复由设备端 AVSpeechSynthesizer 播放，服务端 `tts.chunk` 不启用。

Watch 会议录制 AAC/m4a 30 秒分片，持久化 `meeting_id/seq` 队列并向 `POST /v1/meetings/{id}/chunks?seq=N` 上传原始字节；2xx 后移除本地副本，失败重试。标记在本地持久化并通过 Bearer REST `/marks` 发送（2xx 确认；同 `meeting_id/ts/label` 在网关去重）。`finalize` 提交 `expected_chunks`，防止尾片缺失被默认为成功。电量低于 20% 自动停当前段，回充后需手动新建会议；跨会议段自动合并尚未实现。若录音过程 App 被杀，开放中的 AAC 片可能不完整：恢复时**不自动 finalize**，须由用户确认“仅处理已封存片段”，纪要标记为中断。硬件双击/双指连点打标记尚未完成 Spike，当前用明确按钮。

两端可读取 Markdown 纪要，iPhone 支持系统分享；不提供录音远程控制、对话 UI 或账户。会议列表 JSON `{id,state,created,ready}`；`ready` 为布尔。具体协议与本机测试命令见仓库根目录 `README.md`。需要在真实 Watch/iPhone 上复测麦克风权限、录音后台存活、TTS 计时、断网续传、配对及 URLSession/WebSocket 消息时序，不能把源文件或语法检查视为功能验收。
