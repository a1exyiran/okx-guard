# okx-guard v0.1.0 — 带护栏的 OKX 现货下单工具（AI Builder 返佣）

用户用**自己的** OKX API Key 在**自己的电脑**上运行；每笔订单自动带上产品方的 AI Builder Code（OKX 字段 `tag`），
用户的成交手续费按 OKX 规则返给产品方（批准后 35%，最高 50%，仅 VIP3 及以下用户）。

## 用户能得到什么
1. **体检报告（只读，免费钩子）**：近 90 天现货成交笔数、成交额、手续费合计、手续费/成交额、吃单比例、
   按当前频率一年手续费占权益多少，给红/黄/绿灯。只需要"读取"权限的 Key。
2. **带护栏的下单**：品种白名单、单笔上限、每日笔数、每日买入额、同品种冷却、一键暂停（HALT）、
   只做现货 cash 模式（不借币不加杠杆）、卖出必须写明数量（不会"全部卖出"）、
   幂等防重复下单、结果未知时拒绝重下、JSONL 审计日志（不含密钥）、下单后回查成交与归因。

## 安装与运行
```bash
cd product/okx_guard
../../venv/bin/python -m unittest test_guard.py      # 23 项测试
export OKX_PROFILE=demo                               # 默认模拟盘
export OKX_API_KEY=... OKX_SECRET_KEY=... OKX_PASSPHRASE=...
../../venv/bin/python cli.py report
../../venv/bin/python cli.py --config guard_config.example.json buy BTC-USDT --quote 5
../../venv/bin/python cli.py halt     # 暂停；resume 恢复；log 看最近 20 条审计
```
实盘：`OKX_PROFILE=live` 并加 `--confirm-live`。Key 只开"读取+交易"，**不要开提现**。

## 发布前必须做
- [ ] 产品方用**已认证的 OKX 主账户**申请 AI Builder（子账户不能参加），把 Builder Code 填进 `guard.py` 的 `BUILDER_CODE`。
- [ ] 新建 demo Key（读取+交易），跑 `report` 和一笔 `buy`，确认返回 `tag_ok: true`。
- [ ] 小额实盘 1 笔，到 AI Builder 后台确认这笔成交被识别、佣金被计算；未识别前不要推广。
- [ ] 注意：产品方自己账户的交易不计返佣（规则第 7 条"自营交易"）。

## 文件
| 文件 | 来源 |
|---|---|
| `okx_openapi_client.py` | OKX 官方 builder-integration-demo 原样拷贝（MIT，见 `LICENSE.okx-demo`），不得修改 |
| `guard.py` | 护栏、下单、对账、体检逻辑 |
| `cli.py` | 命令行 |
| `test_guard.py` | 测试（含官方签名已知答案向量） |

## 已知限制（v0.1）
- 只支持现货、acctLv 1/2；只统计 USDT/USDC 计价的成交。
- 体检报告不算盈亏，只看手续费和交易频率。
- 每日限额以本地审计日志为准；换电脑或删日志会重置。
- 只在 OKX 全球站测试设计；其他站点需改 `OKX_API_BASE_URL` 并核对接口。
