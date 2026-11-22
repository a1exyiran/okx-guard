#!/usr/bin/env python3
"""okx-guard 命令行。

  python cli.py report [--days 90]                  # 体检报告（只读）
  python cli.py buy  BTC-USDT --quote 5             # 市价买入，花 5 USDT
  python cli.py sell BTC-USDT --size 0.00005        # 市价卖出指定数量
  python cli.py buy  BTC-USDT --size 0.0001 --px 60000   # 限价
  python cli.py halt | resume | log

环境变量：OKX_PROFILE=demo|live（默认 demo），OKX_API_KEY / OKX_SECRET_KEY / OKX_PASSPHRASE。
实盘须 OKX_PROFILE=live 且加 --confirm-live。API Key 只开"读取+交易"，不要开提现。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal

import guard as g


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="okx-guard")
    p.add_argument("--config", help="JSON 配置文件路径")
    p.add_argument("--confirm-live", action="store_true", help="实盘下单的显式确认")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("report"); r.add_argument("--days", type=int, default=90)
    for side in ("buy", "sell"):
        s = sub.add_parser(side)
        s.add_argument("inst_id")
        s.add_argument("--quote", type=Decimal, help="市价买入花费的计价币数量")
        s.add_argument("--size", type=Decimal, help="基础币数量（卖出/限价必填）")
        s.add_argument("--px", type=Decimal, help="限价；不填即市价")
        s.add_argument("--intent-id", help="幂等键；同一 intent-id 重跑不会重复下单（默认=当前分钟）")
    for name in ("halt", "resume", "log"):
        sub.add_parser(name)
    a = p.parse_args(argv)

    try:
        cfg = g.Config.load(a.config)
        ledger = g.Ledger(cfg)
        if a.cmd == "halt":
            ledger.halt.write_text(datetime.now(timezone.utc).isoformat()); print("已暂停：所有下单将被拒绝"); return 0
        if a.cmd == "resume":
            ledger.halt.unlink(missing_ok=True); print("已恢复"); return 0
        if a.cmd == "log":
            for row in ledger.rows()[-20:]:
                print(json.dumps(row, ensure_ascii=False))
            return 0
        if a.cmd == "report":
            creds = g.Creds.from_env(confirm_live=True)  # 只读，不需要额外确认
            fills = g.fetch_fills(creds, days=a.days)
            code = g.BUILDER_CODE or None
            print(g.render_report(g.analyze(fills, g.equity(creds), a.days, code)))
            return 0
        creds = g.Creds.from_env(confirm_live=a.confirm_live)
        intent = g.Intent(a.inst_id.upper(), a.cmd, "limit" if a.px is not None else "market",
                          quote_amount=a.quote, base_size=a.size, px=a.px)
        nonce = a.intent_id or datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
        res = g.submit(intent, cfg, creds, nonce)
        print(json.dumps(res, ensure_ascii=False, indent=2, default=str))
        if res.get("status") == "submitted" and not res.get("tag_ok"):
            print("警告：订单回查未确认 tag=Builder Code，请到 AI Builder 后台核对归因，暂勿放量", file=sys.stderr)
            return 3
        return 0
    except g.GuardError as e:
        print(f"拒绝：{e}", file=sys.stderr)
        return 2
    except ValueError as e:  # Builder Code 缺失/格式错误等
        print(f"配置错误：{e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
