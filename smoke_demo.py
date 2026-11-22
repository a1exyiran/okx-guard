#!/usr/bin/env python3
"""模拟盘一键自检：Key、签名、权限、账户模式、余额、体检报告，以及带归因的下单与卖回。

  python smoke_demo.py                        # 还没有 Builder Code：只做只读检查
  python smoke_demo.py --builder-code ABC123  # 再加 5 USDT 模拟买入 + 卖回，核对 tag

只用模拟盘（请求头 x-simulated-trading: 1）；env 文件里写了 live 会直接拒绝。
不会打印或保存任何 Key / Secret / Passphrase。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import guard as g
import okx_openapi_client as client

DEFAULT_ENV = "~/.okx-guard/demo.env"
PLACEHOLDERS = {"", "...", "填这里"}
HINTS = {
    "50123": "之前遇到过：这个 demo Key 没有交易权限。请重新创建 demo Key，权限勾选“读取”和“交易”。",
    "50101": "常见原因：Key 与环境不匹配（比如把实盘 Key 用在模拟盘）。请在模拟交易页面创建 Key。",
    "50105": "常见原因：Passphrase 不对。",
    "50110": "常见原因：当前 IP 不在这个 Key 的 IP 白名单里。",
    "50111": "常见原因：API Key 填错或已被删除。",
    "50113": "常见原因：Secret 填错，签名不通过。",
    "51008": "常见原因：模拟盘余额不足。",
}


def with_hint(msg: str) -> str:
    for code, hint in HINTS.items():
        if f"code={code}" in msg or f"sCode={code}" in msg:
            return f"{msg}（{hint}）"
    return msg


def load_env_file(path: str) -> dict:
    p = Path(path).expanduser()
    if not p.exists():
        raise g.GuardError(f"找不到 {p}，请先按 README 创建 demo.env")
    vals = {}
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        if k.startswith("export "):
            k = k[len("export "):].strip()
        vals[k] = v.strip().strip('"').strip("'")
    return vals


def creds_from_env_vals(vals: dict) -> "g.Creds":
    if vals.get("OKX_PROFILE", "demo").strip().lower() != "demo":
        raise g.GuardError("自检只允许模拟盘：env 文件里的 OKX_PROFILE 必须是 demo")
    names = ("OKX_API_KEY", "OKX_SECRET_KEY", "OKX_PASSPHRASE")
    missing = [n for n in names if vals.get(n, "").strip() in PLACEHOLDERS]
    if missing:
        raise g.GuardError("env 文件缺少或未填写: " + ", ".join(missing))
    return g.Creds(vals["OKX_API_KEY"], vals["OKX_SECRET_KEY"], vals["OKX_PASSPHRASE"], simulated=True,
                   base_url=vals.get("OKX_API_BASE_URL", g.DEFAULT_BASE_URL).rstrip("/"))


def env_file_private(path: str):
    try:
        return not (os.stat(Path(path).expanduser()).st_mode & 0o077)
    except OSError:
        return None


class SmokeReport:
    MARK = {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️"}

    def __init__(self, log):
        self.items, self.log = [], log

    def add(self, step: str, result: str, detail: str = "") -> None:
        self.items.append({"step": step, "result": result, "detail": detail})
        self.log(f"{self.MARK[result]} {step}" + (f"：{detail}" if detail else ""))

    @property
    def failed(self) -> bool:
        return any(i["result"] == "FAIL" for i in self.items)


def smoke_run(env_path: str = DEFAULT_ENV, code: str | None = None, inst: str = "BTC-USDT",
              quote: Decimal = Decimal("5"), state_dir: str = "~/.okx-guard/smoke",
              sleep=time.sleep, log=print) -> dict:
    rep = SmokeReport(log)
    out = {"items": rep.items, "report": None, "orders": {}, "result_file": None}
    base_ccy, quote_ccy = inst.split("-")[:2]

    def finish() -> dict:
        out["failed"] = rep.failed
        d = Path(state_dir).expanduser()
        d.mkdir(parents=True, exist_ok=True)
        f = d / ("result-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".json")
        f.write_text(json.dumps({k: v for k, v in out.items() if k != "result_file"},
                                ensure_ascii=False, indent=2, default=str))
        out["result_file"] = str(f)
        return out

    # 1. env 文件
    try:
        creds = creds_from_env_vals(load_env_file(env_path))
        rep.add("读取 demo.env", "PASS", "已强制使用模拟盘")
        if env_file_private(env_path) is False:
            rep.add("文件权限", "WARN", f"其他用户可读，建议执行 chmod 600 {env_path}")
    except Exception as e:  # noqa: BLE001
        rep.add("读取 demo.env", "FAIL", str(e))
        return finish()

    # 2. Builder Code（可选）
    if code:
        try:
            code = client._require_ai_builder_code(code)
        except ValueError as e:
            rep.add("Builder Code 格式", "FAIL", str(e))
            return finish()
        if g.BUILDER_CODE and g.BUILDER_CODE != code:
            rep.add("Builder Code", "FAIL", f"与 guard.py 里填写的 {g.BUILDER_CODE} 不一致")
            return finish()
        g.BUILDER_CODE = code
    elif g.BUILDER_CODE:
        code = g.builder_code()

    # 3. 签名、权限、账户模式
    try:
        data = g._ok(g.read_retry(client.get_account_config, creds.base_url, creds.api_key, creds.secret_key,
                                  creds.passphrase, simulated=True, what="读取账户配置"), "读取账户配置")
        cfg0 = data[0] if data else {}
        rep.add("签名与 Key 有效", "PASS")
    except Exception as e:  # noqa: BLE001
        rep.add("签名与 Key 有效", "FAIL", with_hint(str(e)))
        return finish()
    perm, acct = str(cfg0.get("perm", "")), str(cfg0.get("acctLv", ""))
    if "withdraw" in perm:
        rep.add("Key 权限", "FAIL", f"{perm}：带提现权限，请删除这个 Key 并重新创建（只勾读取+交易）")
        return finish()
    if not perm:
        rep.add("Key 权限", "WARN", "OKX 没有返回权限字段，请在 OKX 页面确认只勾了读取和交易")
    elif "trade" not in perm:
        rep.add("Key 权限", "FAIL" if code else "WARN", f"{perm}：只有读取权限，不能下单")
        if code:
            return finish()
    else:
        rep.add("Key 权限", "PASS", perm)
    if acct in ("1", "2"):
        rep.add("账户模式", "PASS", f"acctLv={acct}，现货 cash 模式")
    else:
        rep.add("账户模式", "FAIL", f"acctLv={acct}：请把模拟盘账户切换到现货/简单模式")
        return finish()

    # 4. 余额
    try:
        bal = g._ok(g.read_retry(client.get_account_balance, creds.base_url, creds.api_key, creds.secret_key,
                                 creds.passphrase, ccy=quote_ccy, simulated=True, what="读取余额"), "读取余额")
        details = (bal[0].get("details") or []) if bal else []
        avail = next((Decimal(d.get("availBal") or "0") for d in details if d.get("ccy") == quote_ccy), Decimal(0))
        enough = avail >= quote
        rep.add(f"{quote_ccy} 可用余额", "PASS" if enough or not code else "FAIL", str(avail))
        if code and not enough:
            return finish()
    except Exception as e:  # noqa: BLE001
        rep.add(f"{quote_ccy} 可用余额", "FAIL", with_hint(str(e)))
        return finish()

    # 5. 体检报告（只读）
    try:
        fills = g.fetch_fills(creds, days=30, sleep=sleep)
        r = g.analyze(fills, g.equity(creds), 30, code or None)
        out["report"] = g.render_report(r)
        rep.add("体检报告（近 30 天，只读）", "PASS",
                "无成交记录，暂不评级" if r["fills"] == 0 else f"{r['fills']} 笔成交，{r['grade']}灯")
    except Exception as e:  # noqa: BLE001
        rep.add("体检报告（近 30 天，只读）", "FAIL", with_hint(str(e)))

    if not code:
        rep.add("带归因下单", "WARN", "没有 Builder Code，已跳过。拿到后运行：python smoke_demo.py --builder-code 你的Code")
        return finish()

    # 6. 模拟买入（独立的状态目录，不占用正式额度）
    cfg = g.Config(allowed_inst=[inst], max_order_quote=Decimal("20"), max_orders_per_day=10,
                   max_daily_buy_quote=Decimal("50"), cooldown_minutes=0, state_dir=state_dir)
    nonce = "smoke" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    try:
        buy = g.submit(g.Intent(inst, "buy", "market", quote_amount=quote), cfg, creds, nonce + "b", sleep=sleep)
    except Exception as e:  # noqa: BLE001
        rep.add(f"模拟买入 {quote} {quote_ccy}", "FAIL", with_hint(str(e)))
        return finish()
    o = buy.get("order") or {}
    out["orders"]["buy"] = {k: o.get(k) for k in ("ordId", "clOrdId", "state", "accFillSz", "avgPx", "fee", "feeCcy", "tag")}
    filled = o.get("state") == "filled"
    rep.add(f"模拟买入 {quote} {quote_ccy}", "PASS" if filled else "FAIL",
            f"状态 {o.get('state')}，成交 {o.get('accFillSz')} @ {o.get('avgPx')}，手续费 {o.get('fee')} {o.get('feeCcy')}")
    rep.add("买单 tag = Builder Code", "PASS" if buy.get("tag_ok") else "FAIL", f"tag={o.get('tag')}")
    if not filled:
        return finish()

    # 7. 卖回（买入手续费从基础币里扣，fee 为负）
    size = Decimal(o.get("accFillSz") or "0")
    if o.get("feeCcy") == base_ccy:
        size += Decimal(o.get("fee") or "0")
    try:
        sell = g.submit(g.Intent(inst, "sell", "market", base_size=size), cfg, creds, nonce + "s", sleep=sleep)
    except Exception as e:  # noqa: BLE001
        rep.add("模拟卖回", "FAIL", with_hint(str(e)) + f"；模拟盘里多出约 {size} {base_ccy}，不影响真钱")
        return finish()
    so = sell.get("order") or {}
    out["orders"]["sell"] = {k: so.get(k) for k in ("ordId", "clOrdId", "state", "accFillSz", "avgPx", "fee", "feeCcy", "tag")}
    rep.add(f"模拟卖回 {size} {base_ccy}", "PASS" if so.get("state") == "filled" else "FAIL",
            f"状态 {so.get('state')}，成交 {so.get('accFillSz')} @ {so.get('avgPx')}，手续费 {so.get('fee')} {so.get('feeCcy')}")
    rep.add("卖单 tag = Builder Code", "PASS" if sell.get("tag_ok") else "FAIL", f"tag={so.get('tag')}")

    # 8. 成交记录里的 tag（可能有几秒延迟）
    ord_ids = {x for x in (buy.get("ordId"), sell.get("ordId")) if x}
    mine = []
    try:
        for i in range(5):
            data = g._ok(g.signed_get(creds, g.PATH_FILLS_HISTORY,
                                      {"instType": "SPOT", "instId": inst, "limit": "100"}), "读取成交记录")
            mine = [f for f in data if f.get("ordId") in ord_ids]
            if {f.get("ordId") for f in mine} >= ord_ids:
                break
            if i < 4:
                sleep(2)
        seen = {f.get("ordId") for f in mine}
        if not mine:
            rep.add("成交记录 tag", "WARN", "成交记录还没同步，稍后到 OKX 页面核对")
        elif all(f.get("tag") == code for f in mine):
            rep.add("成交记录 tag", "PASS" if seen >= ord_ids else "WARN",
                    f"{len(mine)} 条成交都带 tag={code}" + ("" if seen >= ord_ids else "（部分成交还没同步）"))
        else:
            rep.add("成交记录 tag", "FAIL", "有成交的 tag 不是 Builder Code")
    except Exception as e:  # noqa: BLE001
        rep.add("成交记录 tag", "WARN", with_hint(str(e)))
    return finish()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="smoke_demo", description="模拟盘一键自检（只用模拟盘，不动真钱）")
    p.add_argument("--env", default=DEFAULT_ENV, help=f"Key 文件路径，默认 {DEFAULT_ENV}")
    p.add_argument("--builder-code", help="AI Builder Code；不填则只做只读检查")
    p.add_argument("--inst", default="BTC-USDT")
    p.add_argument("--quote", type=Decimal, default=Decimal("5"), help="模拟买入金额，2–20，默认 5")
    p.add_argument("--state-dir", default="~/.okx-guard/smoke")
    a = p.parse_args(argv)
    if not Decimal("2") <= a.quote <= Decimal("20"):
        print("--quote 必须在 2 到 20 之间", file=sys.stderr)
        return 2
    print("okx-guard 模拟盘自检（不会动真钱）\n")
    res = smoke_run(a.env, a.builder_code, a.inst.upper(), a.quote, a.state_dir)
    counts = {k: sum(1 for i in res["items"] if i["result"] == k) for k in ("PASS", "WARN", "FAIL")}
    print(f"\n结果：{counts['PASS']} 项通过，{counts['WARN']} 项提醒，{counts['FAIL']} 项失败")
    print(f"详细结果（不含密钥）：{res['result_file']}")
    if res.get("report"):
        print("\n" + res["report"])
    return 1 if res["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
