# Mac mini 部署草案（尚未执行）

在目标机器安装 Python/uv、ffmpeg、Tailscale、Codex（如需），并按根目录 README 初始化 `~/.btell/config.yaml`（600）。先实测该机器的 Tailscale 100.x 地址、Watch 直连及 BYO 凭据读取；**不要绑定公网接口**。

`com.beetell.hub.plist.template` 是 launchd 用户级 LaunchAgent 模板，不能直接加载：把 `__BEETELL_VENV__`、`__BEETELL_WORKSPACE__`、`__BTELL_HOME__` 替换为目标机器上的绝对路径（不要填密钥），提前建立权限 700 的 `~/.btell/logs`，用 `plutil -lint` 验证，然后将副本放到 `~/Library/LaunchAgents/com.beetell.hub.plist`。在用户 session 中运行 `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.beetell.hub.plist`，通过 `launchctl print gui/$(id -u)/com.beetell.hub` 与 `btell doctor` 检查运行情况。重启/崩溃恢复需要在目标 Mac mini 验证；未登录状态 Keychain 可能锁定。

电源策略 `pmset` 需要所有者授权，**不要从模板自动执行**。将 `~/.btell/` 纳入 Time Machine 时需保护配置、音频和数据库；做一次脱敏恢复演练后再勾选 M4-04。`keep_raw_7d` 的已转写音频在启动或新上传时清理；无后台定时清理时，空闲超过七天的文件直到下次启动/上传才会删除，不应向使用者宣称精确七天销毁。首次配对码通过本机 `btell pair-code` 生成，仅通过受保护网络打开 `/setup`。
