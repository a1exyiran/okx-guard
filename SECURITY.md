# 安全说明 / Security

## 报告漏洞
请不要在公开 issue 里贴漏洞细节或任何 API Key。可以用以下任一方式私下联系维护者：

- GitHub 仓库的 **Security → Report a vulnerability**（私密报告，推荐）
- 邮件：they1rann@gmail.com（标题请以 `[okx-guard security]` 开头）

我们会在 3 个工作日内回复。邮件里同样**不要**附带 API Key、Secret 或 Passphrase。

**English:** Please report vulnerabilities privately via GitHub's "Report a vulnerability" or by email to they1rann@gmail.com (subject prefix `[okx-guard security]`). Never include API keys or secrets.

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
