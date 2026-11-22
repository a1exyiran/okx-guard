# AI Builder 归因说明

## 是什么
OKX AI Builder Program 允许开发者在通过自己产品下的订单里带上 AI Builder Code，
OKX 会把符合条件的成交手续费按比例返给开发者。用户的手续费不会因此增加。

官方说明：
- [AI Builder Program](https://www.okx.com/agent-tradekit/builder)
- [接入指南](https://www.okx.com/help/ai-builder-program-integration-guide)
- [佣金规则](https://www.okx.com/help/ai-builder-program-commission-rules)

## 在代码里的位置
- `guard.py` 顶部的 `BUILDER_CODE`：开发者的 Builder Code，不是密钥，可以公开。
- 下单时通过官方客户端写入请求字段 `tag`（OpenAPI 直连方式使用 `tag`）。
- Builder Code 为空或格式错误时，下单在发出任何请求前就报错，不会静默下一笔未归因的单。
- 下单后回查订单，确认 `tag` 等于 Builder Code；不一致时命令以退出码 `3` 结束。

## 用户可以怎么做
- **自行审查**：搜索 `BUILDER_CODE` 和 `tag`，只有这两处与归因有关。
- **不想带归因**：只用 `report`（只读，不涉及归因），或 fork 后自行修改。本项目不会隐藏或混淆这段逻辑。

## 不计返佣的情况（摘自官方规则）
- 用户费率等级 VIP4 及以上；
- 开发者自己账户的交易；
- 使用特殊费率、抵扣券或托管子账户的交易；
- 未带或带错 Builder Code 的订单。
