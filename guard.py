"""okx-guard: 带护栏的 OKX 现货下单工具（AI Builder 归因）。

设计原则
- 签名/请求只走 okx_openapi_client.py（OKX 官方 demo 原样拷贝，不手写 HMAC）。
- 每笔下单必须带 AI Builder Code（OKX 字段 tag）；缺失或格式错误直接报错，绝不默认。
- 默认模拟盘；实盘必须 OKX_PROFILE=live 且命令行显式 --confirm-live。
- 只做现货、只用 cash 模式（acctLv 1/2），不借币、不加杠杆。
- 护栏：品种白名单、单笔上限、每日笔数、每日买入额、同品种冷却、HALT 文件、文件锁。
- 幂等：clOrdId 由意图确定性生成；下单前先按 clOrdId 查询，已存在则不重复下单。
- 审计：每次尝试写一行 JSONL（不写任何密钥）。
"""
from __future__ import annotations

import contextlib
import decimal
import fcntl
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from pathlib import Path

import okx_openapi_client as client

# ---- 产品方（你）的 AI Builder Code：申请后填入。不是密钥，可公开。----
# 不从环境变量读取，避免部署环境误覆盖导致归因错误（与 OKX demo 一致）。
BUILDER_CODE = ""

VERSION = "0.1.0"
DEFAULT_BASE_URL = "https://www.okx.com"
PATH_TRADE_ORDER = client.PATH_TRADE_ORDER  # GET 同一路径 = 查询订单
PATH_FILLS_HISTORY = "/api/v5/trade/fills-history"
CLORDID_RE = re.compile(r"^[A-Za-z0-9]{1,32}$")
MIN_NOTIONAL_FALLBACK = Decimal("1")  # demo 实测 51020：约 1 USDT


class GuardError(Exception):
    """可预期的拒绝（护栏/前置检查/配置）。"""


# --------------------------------------------------------------------------- 配置
@dataclass
class Config:
    allowed_inst: list = field(default_factory=lambda: ["BTC-USDT", "ETH-USDT"])
    max_order_quote: Decimal = Decimal("20")
    max_orders_per_day: int = 3
    max_daily_buy_quote: Decimal = Decimal("50")
    cooldown_minutes: int = 60
    allowed_ord_types: list = field(default_factory=lambda: ["market", "limit"])
    state_dir: str = "~/.okx-guard"

    @classmethod
    def load(cls, path: str | None) -> "Config":
        cfg = cls()
        if path:
            raw = json.loads(Path(path).expanduser().read_text())
            for k, v in raw.items():
                if not hasattr(cfg, k):
                    raise GuardError(f"未知配置项: {k}")
                cur = getattr(cfg, k)
                setattr(cfg, k, Decimal(str(v)) if isinstance(cur, Decimal) else v)
        if cfg.max_order_quote <= 0 or cfg.max_orders_per_day <= 0:
            raise GuardError("限额必须为正数")
        return cfg

    @property
    def dir(self) -> Path:
        d = Path(self.state_dir).expanduser()
        d.mkdir(parents=True, exist_ok=True)
        return d


# --------------------------------------------------------------------------- 凭证
@dataclass
class Creds:
    api_key: str
    secret_key: str
    passphrase: str
    simulated: bool
    base_url: str = DEFAULT_BASE_URL

    @classmethod
    def from_env(cls, confirm_live: bool = False) -> "Creds":
        profile = os.environ.get("OKX_PROFILE", "demo").strip().lower()
        if profile not in ("demo", "live"):
            raise GuardError("OKX_PROFILE 只能是 demo 或 live")
        if profile == "live" and not confirm_live:
            raise GuardError("实盘需要显式加 --confirm-live")
        missing = [n for n in ("OKX_API_KEY", "OKX_SECRET_KEY", "OKX_PASSPHRASE") if not os.environ.get(n)]
        if missing:
            raise GuardError("缺少环境变量: " + ", ".join(missing))
        return cls(os.environ["OKX_API_KEY"], os.environ["OKX_SECRET_KEY"], os.environ["OKX_PASSPHRASE"],
                   simulated=(profile == "demo"),
                   base_url=os.environ.get("OKX_API_BASE_URL", DEFAULT_BASE_URL).rstrip("/"))


def builder_code() -> str:
    # 复用官方校验：缺失/占位/格式错误直接抛错。
    return client._require_ai_builder_code(BUILDER_CODE)


# --------------------------------------------------------------------------- 签名 GET（复用官方签名助手）
def signed_get(creds: Creds, path: str, params: dict) -> dict:
    request_path = client._with_query(path, params)
    headers = client._headers(creds.api_key, creds.secret_key, creds.passphrase,
                              "GET", request_path, "", creds.simulated)
    return client._parse(client.requests.get(creds.base_url + request_path, headers=headers, timeout=10))


def _ok(resp: dict, what: str) -> list:
    if not isinstance(resp, dict) or resp.get("code") != "0":
        code = resp.get("code") if isinstance(resp, dict) else None
        msg = resp.get("msg") if isinstance(resp, dict) else str(resp)[:200]
        sub = ""
        if isinstance(resp, dict) and resp.get("data"):
            d0 = resp["data"][0] if isinstance(resp["data"], list) else {}
            sub = f" sCode={d0.get('sCode')} sMsg={d0.get('sMsg')}"
        raise GuardError(f"{what} 失败: code={code} msg={msg}{sub}")
    return resp.get("data") or []


# --------------------------------------------------------------------------- 本地账本 / 审计
class Ledger:
    def __init__(self, cfg: Config):
        self.path = cfg.dir / "audit.jsonl"
        self.halt = cfg.dir / "HALT"
        self.lock_path = cfg.dir / "lock"

    def rows(self) -> list:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text().splitlines():
            with contextlib.suppress(ValueError):
                out.append(json.loads(line))
        return out

    def append(self, row: dict) -> None:
        row = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "v": VERSION, **row}
        with self.path.open("a") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            f.flush()
            os.fsync(f.fileno())

    @contextlib.contextmanager
    def locked(self):
        with self.lock_path.open("w") as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise GuardError("另一个 okx-guard 进程正在运行")
            try:
                yield
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)


# --------------------------------------------------------------------------- 护栏（纯函数，便于测试）
@dataclass
class Intent:
    inst_id: str
    side: str            # buy / sell
    ord_type: str        # market / limit
    quote_amount: Decimal | None = None   # 市价买：花多少计价币
    base_size: Decimal | None = None      # 卖出 或 限价买：多少基础币
    px: Decimal | None = None             # 限价


def _local_day(ts_iso: str) -> str:
    return datetime.fromisoformat(ts_iso).astimezone().date().isoformat()


def evaluate(intent: Intent, notional: Decimal, cfg: Config, rows: list, now: datetime,
             halted: bool) -> list:
    """返回违规原因列表；空列表=放行。统计已提交及结果未知的订单（保守计入限额）。"""
    v = []
    if halted:
        v.append("HALT 文件存在，已暂停所有下单")
    if intent.inst_id not in cfg.allowed_inst:
        v.append(f"{intent.inst_id} 不在白名单 {cfg.allowed_inst}")
    if intent.side not in ("buy", "sell"):
        v.append("side 只能是 buy/sell")
    if intent.ord_type not in cfg.allowed_ord_types:
        v.append(f"订单类型 {intent.ord_type} 不允许")
    if notional > cfg.max_order_quote:
        v.append(f"单笔 {notional} 超过上限 {cfg.max_order_quote}")
    today = now.astimezone().date().isoformat()
    sent = [r for r in rows if r.get("status") in ("submitted", "submit_unknown")]
    today_rows = [r for r in sent if _local_day(r["ts"]) == today]
    if len(today_rows) >= cfg.max_orders_per_day:
        v.append(f"今日已下 {len(today_rows)} 笔，达到上限 {cfg.max_orders_per_day}")
    if intent.side == "buy":
        spent = sum((Decimal(str(r.get("notional", "0"))) for r in today_rows if r.get("side") == "buy"), Decimal("0"))
        if spent + notional > cfg.max_daily_buy_quote:
            v.append(f"今日买入 {spent}+{notional} 超过每日上限 {cfg.max_daily_buy_quote}")
    same = [r for r in sent if r.get("inst_id") == intent.inst_id]
    if same and cfg.cooldown_minutes > 0:
        last = max(datetime.fromisoformat(r["ts"]) for r in same)
        mins = (now - last).total_seconds() / 60
        if mins < cfg.cooldown_minutes:
            v.append(f"{intent.inst_id} 冷却中：距上次 {mins:.0f} 分钟 < {cfg.cooldown_minutes}")
    return v


def make_cl_ord_id(intent: Intent, nonce: str) -> str:
    key = f"{intent.inst_id}|{intent.side}|{intent.ord_type}|{intent.quote_amount}|{intent.base_size}|{intent.px}|{nonce}"
    cid = "g" + hashlib.sha256(key.encode()).hexdigest()[:24]
    assert CLORDID_RE.match(cid)
    return cid


def round_down(x: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        raise GuardError("lotSz 非法")
    return (x / step).to_integral_value(rounding=ROUND_DOWN) * step


# --------------------------------------------------------------------------- 下单流程
def preflight(creds: Creds, inst_id: str) -> dict:
    cfg_data = _ok(client.get_account_config(creds.base_url, creds.api_key, creds.secret_key,
                                             creds.passphrase, simulated=creds.simulated), "读取账户配置")
    acct_lv = str(cfg_data[0].get("acctLv")) if cfg_data else ""
    if acct_lv not in ("1", "2"):
        raise GuardError(f"账户模式 acctLv={acct_lv}：本工具只支持现货 cash 模式（acctLv 1/2），以免借币")
    inst = _ok(client.get_instruments(creds.base_url, "SPOT", inst_id, simulated=creds.simulated), "读取品种规则")
    if not inst:
        raise GuardError(f"找不到品种 {inst_id}")
    inst = inst[0]
    if inst.get("state") not in (None, "", "live"):
        raise GuardError(f"{inst_id} 状态 {inst.get('state')}，不可交易")
    tick = _ok(client.get_ticker(creds.base_url, inst_id, simulated=creds.simulated), "读取行情")
    last = Decimal(tick[0]["last"])
    return {"acctLv": acct_lv, "tdMode": "cash", "last": last,
            "lotSz": Decimal(inst["lotSz"]), "minSz": Decimal(inst["minSz"]),
            "baseCcy": inst.get("baseCcy"), "quoteCcy": inst.get("quoteCcy")}


def size_order(intent: Intent, pf: dict) -> tuple:
    """返回 (sz, tgtCcy, notional_quote)。"""
    if intent.ord_type == "market" and intent.side == "buy":
        if intent.quote_amount is None or intent.base_size is not None:
            raise GuardError("市价买入请用 --quote（花多少计价币），不要给 --size")
        notional = intent.quote_amount
        return str(notional.normalize()), "quote_ccy", notional
    if intent.base_size is None:
        raise GuardError("卖出/限价单必须明确给出 --size（基础币数量）；本工具不会按'全部可用余额'卖出")
    sz = round_down(intent.base_size, pf["lotSz"])
    if sz < pf["minSz"] or sz <= 0:
        raise GuardError(f"数量 {sz} 小于最小下单量 {pf['minSz']}")
    price = intent.px if intent.ord_type == "limit" else pf["last"]
    if intent.ord_type == "limit" and intent.px is None:
        raise GuardError("限价单必须给 --px")
    return str(sz.normalize()), "base_ccy", (sz * price).quantize(Decimal("0.00000001"))


def find_order(creds: Creds, inst_id: str, cl_ord_id: str) -> dict | None:
    resp = signed_get(creds, PATH_TRADE_ORDER, {"instId": inst_id, "clOrdId": cl_ord_id})
    if isinstance(resp, dict) and resp.get("code") == "0" and resp.get("data"):
        return resp["data"][0]
    return None  # 51603 等 = 不存在；其他错误也按不存在处理，但不会重复提交（见下）


def submit(intent: Intent, cfg: Config, creds: Creds, nonce: str, now: datetime | None = None,
           poll: int = 5, sleep=time.sleep) -> dict:
    code = builder_code()
    ledger = Ledger(cfg)
    with ledger.locked():
        pf = preflight(creds, intent.inst_id)
        sz, tgt, notional = size_order(intent, pf)
        if notional < MIN_NOTIONAL_FALLBACK:
            raise GuardError(f"名义金额 {notional} 低于约 1 {pf['quoteCcy']} 的最小下单额")
        cid = make_cl_ord_id(intent, nonce)
        base_row = {"inst_id": intent.inst_id, "side": intent.side, "ord_type": intent.ord_type,
                    "sz": sz, "tgtCcy": tgt, "notional": str(notional), "clOrdId": cid,
                    "simulated": creds.simulated}
        existing = find_order(creds, intent.inst_id, cid)
        if existing:
            ledger.append({**base_row, "status": "duplicate_skipped", "ordId": existing.get("ordId")})
            return {"status": "duplicate_skipped", "order": existing}
        rows = ledger.rows()
        if any(r.get("clOrdId") == cid and r.get("status") in ("submitted", "submit_unknown") for r in rows):
            ledger.append({**base_row, "status": "blocked", "reasons": ["同一意图此前已提交但查询不到，需人工核对"]})
            raise GuardError("同一 clOrdId 此前已提交但查询不到，拒绝重复下单，请人工核对")
        now = now or datetime.now(timezone.utc)
        reasons = evaluate(intent, notional, cfg, rows, now, ledger.halt.exists())
        if reasons:
            ledger.append({**base_row, "status": "blocked", "reasons": reasons})
            raise GuardError("护栏拒绝：" + "；".join(reasons))
        try:
            resp = client.place_order(creds.base_url, creds.api_key, creds.secret_key, creds.passphrase,
                                      intent.inst_id, pf["tdMode"], intent.side, intent.ord_type, sz,
                                      px=str(intent.px) if intent.px is not None else None,
                                      tgt_ccy=tgt, cl_ord_id=cid, ai_builder_code=code,
                                      simulated=creds.simulated)
        except Exception as e:  # 网络异常：结果未知，记为 submit_unknown 并计入限额
            ledger.append({**base_row, "status": "submit_unknown", "error": repr(e)[:200]})
            raise GuardError(f"提交结果未知（{e!r}），已记录；下次同一意图会先查询，不会重复下单")
        if resp.get("code") != "0":
            ledger.append({**base_row, "status": "rejected", "resp": resp})
            _ok(resp, "下单")
        ord_id = resp["data"][0].get("ordId")
        ledger.append({**base_row, "status": "submitted", "ordId": ord_id})
        final = reconcile(creds, intent.inst_id, cid, poll=poll, sleep=sleep)
        tag_ok = final is not None and final.get("tag") == code
        ledger.append({**base_row, "status": "reconciled", "ordId": ord_id,
                       "state": (final or {}).get("state"), "accFillSz": (final or {}).get("accFillSz"),
                       "avgPx": (final or {}).get("avgPx"), "fee": (final or {}).get("fee"),
                       "feeCcy": (final or {}).get("feeCcy"), "tag_ok": tag_ok})
        return {"status": "submitted", "ordId": ord_id, "clOrdId": cid, "order": final, "tag_ok": tag_ok}


def reconcile(creds: Creds, inst_id: str, cid: str, poll: int = 5, sleep=time.sleep) -> dict | None:
    last = None
    for i in range(max(poll, 1)):
        last = find_order(creds, inst_id, cid) or last
        if last and last.get("state") in ("filled", "canceled", "mmp_canceled"):
            return last
        if i + 1 < poll:
            sleep(1)
    return last


# --------------------------------------------------------------------------- 体检报告
def fetch_fills(creds: Creds, days: int = 90, max_pages: int = 50, sleep=time.sleep) -> list:
    begin = str(int((time.time() - days * 86400) * 1000))
    out, after = [], None
    for _ in range(max_pages):
        data = _ok(signed_get(creds, PATH_FILLS_HISTORY,
                              {"instType": "SPOT", "begin": begin, "after": after, "limit": "100"}), "读取成交记录")
        out.extend(data)
        if len(data) < 100:
            break
        after = data[-1]["billId"]
        sleep(0.25)  # 限频 10 次/2 秒
    return out


def analyze(fills: list, equity_quote: Decimal | None, days: int, code: str | None = None) -> dict:
    stable = {"USDT", "USDC"}
    turnover, fees, n, taker, tagged, skipped = Decimal(0), Decimal(0), 0, 0, 0, 0
    orders, active_days = set(), set()
    for f in fills:
        base, quote = f["instId"].split("-")[:2]
        if quote not in stable:
            skipped += 1
            continue
        px, sz = Decimal(f["fillPx"]), Decimal(f["fillSz"])
        fee = Decimal(f.get("fee") or "0")  # 负=付费，正=返还
        fee_quote = fee if f.get("feeCcy") == quote else (fee * px if f.get("feeCcy") == base else Decimal(0))
        turnover += px * sz
        fees += -fee_quote
        n += 1
        taker += 1 if f.get("execType") == "T" else 0
        tagged += 1 if code and f.get("tag") == code else 0
        orders.add(f.get("ordId"))
        active_days.add(datetime.fromtimestamp(int(f.get("fillTime") or f["ts"]) / 1000).date())
    res = {
        "days": days, "fills": n, "orders": len(orders), "active_days": len(active_days),
        "turnover": turnover.quantize(Decimal("0.01")), "fees": fees.quantize(Decimal("0.0001")),
        "fee_bps": (fees / turnover * 10000).quantize(Decimal("0.1")) if turnover else Decimal(0),
        "taker_ratio": (Decimal(taker) / n).quantize(Decimal("0.01")) if n else Decimal(0),
        "orders_per_active_day": (Decimal(len(orders)) / len(active_days)).quantize(Decimal("0.1")) if active_days else Decimal(0),
        "skipped_non_stable": skipped, "tagged_fills": tagged,
    }
    if equity_quote and equity_quote > 0:
        res["equity"] = equity_quote.quantize(Decimal("0.01"))
        res["annual_fee_pct_of_equity"] = (fees / equity_quote * Decimal(365) / Decimal(days) * 100).quantize(Decimal("0.1"))
    res["grade"] = grade(res)
    return res


def grade(r: dict) -> str:
    red = r.get("annual_fee_pct_of_equity", Decimal(0)) > 10 or r["orders_per_active_day"] > 10
    yellow = r.get("annual_fee_pct_of_equity", Decimal(0)) > 3 or r["orders_per_active_day"] > 3 or r["taker_ratio"] > Decimal("0.8")
    return "红" if red else ("黄" if yellow else "绿")


def render_report(r: dict) -> str:
    lines = [f"# 交易体检报告（近 {r['days']} 天，现货，USDT/USDC 计价）", "",
             f"**结论：{r['grade']}灯**", "",
             "| 指标 | 数值 |", "|---|---|",
             f"| 成交笔数 / 订单数 | {r['fills']} / {r['orders']} |",
             f"| 有交易的天数 | {r['active_days']} |",
             f"| 每个交易日下单数 | {r['orders_per_active_day']} |",
             f"| 成交额 | {r['turnover']} |",
             f"| 手续费合计 | {r['fees']} |",
             f"| 手续费 / 成交额 | {r['fee_bps']} 基点 |",
             f"| 吃单（taker）比例 | {r['taker_ratio']} |"]
    if "equity" in r:
        lines += [f"| 当前权益 | {r['equity']} |",
                  f"| 按此频率，一年手续费占权益 | {r['annual_fee_pct_of_equity']}% |"]
    lines += ["", "说明：红=年化手续费超过权益 10% 或每天超过 10 单；黄=超过 3% 或每天超过 3 单或吃单超过 80%。",
              "只读取成交记录，不下单；非 USDT/USDC 计价的成交未计入"
              + (f"（{r['skipped_non_stable']} 笔）" if r["skipped_non_stable"] else "") + "。"]
    return "\n".join(lines)


def equity(creds: Creds) -> Decimal | None:
    try:
        d = _ok(client.get_account_balance(creds.base_url, creds.api_key, creds.secret_key,
                                           creds.passphrase, simulated=creds.simulated), "读取余额")
        return Decimal(d[0].get("totalEq") or "0") if d else None
    except (GuardError, decimal.InvalidOperation):
        return None
