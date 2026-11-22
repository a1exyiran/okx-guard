# 配置说明

用 `--config 路径` 传入 JSON 文件，未写的项使用默认值，写了未知项会报错。

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `allowed_inst` | `["BTC-USDT", "ETH-USDT"]` | 允许交易的现货品种 |
| `max_order_quote` | `20` | 单笔名义金额上限（计价币） |
| `max_orders_per_day` | `3` | 每个本地自然日最多下单笔数 |
| `max_daily_buy_quote` | `50` | 每个本地自然日买入总额上限 |
| `cooldown_minutes` | `60` | 同一品种两次下单的最小间隔，`0` 为关闭 |
| `allowed_ord_types` | `["market", "limit"]` | 允许的订单类型 |
| `state_dir` | `~/.okx-guard` | 审计日志、HALT 文件、锁文件所在目录 |

## 环境变量

| 变量 | 说明 |
|---|---|
| `OKX_PROFILE` | `demo`（默认）或 `live` |
| `OKX_API_KEY` / `OKX_SECRET_KEY` / `OKX_PASSPHRASE` | 你自己的 OKX API Key |
| `OKX_API_BASE_URL` | 默认 `https://www.okx.com`，其他站点自行设置 |

## 建议
- 先保持默认限额跑一周，再按需要调高。
- 想让某个品种完全停手，从 `allowed_inst` 里删掉即可。
