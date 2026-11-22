# 安全说明 / Security

## 报告漏洞
请不要在公开 issue 里贴漏洞细节或任何 API Key。请通过 GitHub 的
**Security → Report a vulnerability**（私密报告）联系维护者。

## 使用者须知
- API Key 只开"读取"或"读取 + 交易"，**永远不要开提现**。
- 建议在 OKX 为 Key 绑定 IP 白名单。
- 先用模拟盘 Key 测试；实盘需要 `OKX_PROFILE=live` 且命令加 `--confirm-live`。
- Key 放在 `chmod 600` 的本地文件里，不要写进代码、截图或聊天记录。
- 如果怀疑 Key 泄露，立刻在 OKX 删除该 Key。

## 本工具不会做的事
- 不会把你的 Key 发送到 OKX 以外的任何地方。
- 不会在审计日志里记录 Key、Secret、Passphrase 或签名。
- 不会划转、提现、借币或开杠杆。
