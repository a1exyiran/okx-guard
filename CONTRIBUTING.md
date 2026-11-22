# 参与贡献 / Contributing

感谢你愿意改进 okx-guard。

## 基本原则
1. **安全优先。** 任何改动都不能放宽默认限额、绕过 `--confirm-live`，也不能引入提现、划转、借币或杠杆。
2. **不改签名代码。** `okx_openapi_client.py` 原样来自 OKX 官方示例，CI 会校验它的哈希。需要新接口时，在 `guard.py` 里复用它的签名助手。
3. **不记录密钥。** 日志、异常信息、测试快照里都不能出现 Key、Secret、Passphrase 或签名。
4. **不要手写接口字段。** 新接口请对照 [OKX API 文档](https://www.okx.com/docs-v5/) 或 [okx/ai-builder-openapi-md](https://github.com/okx/ai-builder-openapi-md)。

## 开发流程
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m unittest -v test_guard.py
```
- 新功能请附带测试；测试必须离线运行（用 `FakeOKX` 模拟交易所）。
- 一个 PR 只做一件事，在 `CHANGELOG.md` 的 `[Unreleased]` 下写一行。

## 提 issue
- Bug：说明命令、期望结果、实际结果和退出码。**贴日志前请删掉所有密钥。**
- 安全问题：不要公开提，见 [SECURITY.md](SECURITY.md)。

---

**English:** Safety first: never loosen default limits, bypass `--confirm-live`, or add withdrawal, transfer, borrowing or leverage. Do not modify `okx_openapi_client.py` (CI checks its hash). Never log secrets. Every change needs offline tests. Report security issues privately (see SECURITY.md).
