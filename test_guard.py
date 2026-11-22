import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch
from urllib.parse import urlparse, parse_qs

import okx_openapi_client as client
import guard as g


class FakeResp:
    def __init__(self, payload, status=200):
        self._p, self.status_code, self.text = payload, status, json.dumps(payload)

    def json(self):
        return self._p


class FakeOKX:
    """最小假交易所：记录所有请求，按 clOrdId 存订单。"""

    def __init__(self, acct_lv="1", last="50000", fills=None):
        self.acct_lv, self.last = acct_lv, last
        self.orders, self.posts, self.gets = {}, [], []
        self.fills = fills or []
        self.fail_post = None

    def get(self, url, headers=None, timeout=None):
        u = urlparse(url); q = {k: v[0] for k, v in parse_qs(u.query).items()}
        self.gets.append((u.path, q, headers))
        if u.path == client.PATH_ACCOUNT_CONFIG:
            return FakeResp({"code": "0", "data": [{"acctLv": self.acct_lv, "posMode": "net_mode"}]})
        if u.path == client.PATH_PUBLIC_INSTRUMENTS:
            return FakeResp({"code": "0", "data": [{"instId": q["instId"], "lotSz": "0.00000001",
                                                    "minSz": "0.00001", "state": "live",
                                                    "baseCcy": "BTC", "quoteCcy": "USDT"}]})
        if u.path == client.PATH_MARKET_TICKER:
            return FakeResp({"code": "0", "data": [{"last": self.last}]})
        if u.path == g.PATH_TRADE_ORDER:
            o = self.orders.get(q.get("clOrdId"))
            return FakeResp({"code": "0", "data": [o]} if o else {"code": "51603", "msg": "Order does not exist", "data": []})
        if u.path == g.PATH_FILLS_HISTORY:
            after = q.get("after")
            idx = 0 if after is None else next(i for i, f in enumerate(self.fills) if f["billId"] == after) + 1
            return FakeResp({"code": "0", "data": self.fills[idx:idx + int(q.get("limit", 100))]})
        if u.path == client.PATH_ACCOUNT_BALANCE:
            return FakeResp({"code": "0", "data": [{"totalEq": "100"}]})
        raise AssertionError(u.path)

    def post(self, url, headers=None, data=None, timeout=None):
        body = json.loads(data); self.posts.append((url, body, headers, data))
        if self.fail_post:
            raise self.fail_post
        oid = str(1000 + len(self.posts))
        self.orders[body["clOrdId"]] = {"ordId": oid, "clOrdId": body["clOrdId"], "state": "filled",
                                        "tag": body.get("tag"), "accFillSz": "0.0001", "avgPx": self.last,
                                        "fee": "-0.0000001", "feeCcy": "BTC"}
        return FakeResp({"code": "0", "data": [{"ordId": oid, "sCode": "0"}]})


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = g.Config(state_dir=self.tmp.name)
        self.creds = g.Creds("k", "s", "p", simulated=True)
        self.ex = FakeOKX()
        self.p1 = patch.object(client.requests, "get", side_effect=self.ex.get)
        self.p2 = patch.object(client.requests, "post", side_effect=self.ex.post)
        self.p3 = patch.object(g, "BUILDER_CODE", "Y1RAN01")
        for p in (self.p1, self.p2, self.p3):
            p.start()

    def tearDown(self):
        for p in (self.p1, self.p2, self.p3):
            p.stop()
        self.tmp.cleanup()

    def buy(self, quote="5", nonce="n1", inst="BTC-USDT", **kw):
        return g.submit(g.Intent(inst, "buy", "market", quote_amount=Decimal(quote)), self.cfg, self.creds,
                        nonce, poll=1, sleep=lambda s: None, **kw)


class SigningVectors(unittest.TestCase):
    # 来自 OKX 官方 demo test_known_answer_signing_vectors（README 要求拷贝）
    def test_known_answer(self):
        s, ts, path = "mock-secret", "2020-12-08T09:08:57.715Z", "/api/v5/account/balance"
        self.assertEqual(client._sign(s, ts, "GET", path, ""), "tpQYvXdaAfU8ae6zI1rJ2xVcyMIk9BKWK/fysaanweQ=")
        self.assertEqual(client._sign(s, ts, "GET", path + "?ccy=BTC", ""), "pS6nHuBl6Qc9S0h+soCkCVHaVHZzS19KqFpeI/doTlE=")

    def test_signed_get_signs_full_query(self):
        captured = {}
        with patch.object(client, "_now_iso_ms", return_value="2026-08-06T00:00:00.000Z"), \
             patch.object(client.requests, "get", side_effect=lambda url, headers=None, timeout=None:
                          captured.update(url=url, h=headers) or FakeResp({"code": "0", "data": []})):
            g.signed_get(g.Creds("k", "sec", "p", True), g.PATH_FILLS_HISTORY, {"instType": "SPOT", "after": None})
        rp = g.PATH_FILLS_HISTORY + "?instType=SPOT"
        self.assertTrue(captured["url"].endswith(rp))
        self.assertEqual(captured["h"]["OK-ACCESS-SIGN"], client._sign("sec", "2026-08-06T00:00:00.000Z", "GET", rp, ""))
        self.assertEqual(captured["h"]["x-simulated-trading"], "1")


class Attribution(Base):
    def test_order_carries_builder_code_tag_and_sig_over_sent_body(self):
        res = self.buy()
        url, body, headers, raw = self.ex.posts[0]
        self.assertEqual(body["tag"], "Y1RAN01")
        self.assertEqual(body["tdMode"], "cash")
        self.assertEqual(body["tgtCcy"], "quote_ccy")
        self.assertEqual(body["sz"], "5")
        self.assertTrue(res["tag_ok"])
        self.assertEqual(headers["x-simulated-trading"], "1")

    def test_missing_builder_code_fails_before_any_request(self):
        with patch.object(g, "BUILDER_CODE", ""):
            with self.assertRaises(ValueError):
                self.buy()
        self.assertEqual(self.ex.posts, []); self.assertEqual(self.ex.gets, [])

    def test_invalid_builder_code(self):
        with patch.object(g, "BUILDER_CODE", "bad-code!"):
            with self.assertRaises(ValueError):
                self.buy()


class Guardrails(Base):
    def test_over_max_order(self):
        with self.assertRaises(g.GuardError):
            self.buy(quote="25")
        self.assertEqual(self.ex.posts, [])

    def test_not_whitelisted(self):
        with self.assertRaises(g.GuardError):
            self.buy(inst="PEPE-USDT")

    def test_halt(self):
        g.Ledger(self.cfg).halt.write_text("x")
        with self.assertRaises(g.GuardError):
            self.buy()
        self.assertEqual(self.ex.posts, [])

    def test_cooldown_and_daily_count(self):
        self.cfg.cooldown_minutes = 60
        self.buy(nonce="a")
        with self.assertRaisesRegex(g.GuardError, "冷却"):
            self.buy(nonce="b")
        self.cfg.cooldown_minutes = 0
        self.buy(nonce="c"); self.buy(nonce="d")
        with self.assertRaisesRegex(g.GuardError, "上限"):
            self.buy(nonce="e")
        self.assertEqual(len(self.ex.posts), 3)

    def test_daily_buy_cap(self):
        self.cfg.cooldown_minutes = 0; self.cfg.max_daily_buy_quote = Decimal("12")
        self.buy(quote="5", nonce="a"); self.buy(quote="5", nonce="b")
        with self.assertRaisesRegex(g.GuardError, "每日上限"):
            self.buy(quote="5", nonce="c")

    def test_margin_account_refused(self):
        self.ex.acct_lv = "3"
        with self.assertRaisesRegex(g.GuardError, "acctLv"):
            self.buy()

    def test_min_notional(self):
        with self.assertRaises(g.GuardError):
            self.buy(quote="0.5")

    def test_sell_requires_explicit_size_and_rounds_down(self):
        with self.assertRaisesRegex(g.GuardError, "--size"):
            g.submit(g.Intent("BTC-USDT", "sell", "market"), self.cfg, self.creds, "s", poll=1, sleep=lambda s: None)
        res = g.submit(g.Intent("BTC-USDT", "sell", "market", base_size=Decimal("0.000123456789")),
                       self.cfg, self.creds, "s2", poll=1, sleep=lambda s: None)
        self.assertEqual(self.ex.posts[-1][1]["sz"], "0.00012345")
        self.assertEqual(self.ex.posts[-1][1]["tgtCcy"], "base_ccy")
        self.assertEqual(res["status"], "submitted")

    def test_rejected_order_not_counted(self):
        self.cfg.cooldown_minutes = 0
        orig = self.ex.post
        self.ex.post = lambda *a, **k: FakeResp({"code": "1", "msg": "x", "data": [{"sCode": "51008", "sMsg": "bal"}]})
        with patch.object(client.requests, "post", side_effect=self.ex.post):
            for n in ("a", "b", "c", "d"):
                with self.assertRaisesRegex(g.GuardError, "51008"):
                    self.buy(nonce=n)
        self.ex.post = orig
        self.buy(nonce="z")  # 被拒的单不占额度


class Idempotency(Base):
    def test_same_intent_not_resubmitted(self):
        self.buy(nonce="same")
        res = self.buy(nonce="same")
        self.assertEqual(res["status"], "duplicate_skipped")
        self.assertEqual(len(self.ex.posts), 1)

    def test_unknown_result_blocks_resubmit_and_counts(self):
        self.ex.fail_post = ConnectionError("timeout")
        with self.assertRaisesRegex(g.GuardError, "未知"):
            self.buy(nonce="u")
        self.ex.fail_post = None
        with self.assertRaisesRegex(g.GuardError, "人工核对"):
            self.buy(nonce="u")
        self.assertEqual(len(self.ex.posts), 1)
        rows = g.Ledger(self.cfg).rows()
        self.assertIn("submit_unknown", [r["status"] for r in rows])

    def test_clordid_format(self):
        cid = g.make_cl_ord_id(g.Intent("BTC-USDT", "buy", "market", quote_amount=Decimal(5)), "x")
        self.assertRegex(cid, r"^[A-Za-z0-9]{1,32}$")

    def test_audit_has_no_secrets(self):
        self.buy()
        text = (g.Ledger(self.cfg).path).read_text()
        for secret in ('"k"', '"s"', '"p"', "OK-ACCESS"):
            self.assertNotIn(secret, text)


class Report(unittest.TestCase):
    def fills(self):
        t = int(datetime(2026, 11, 1, tzinfo=timezone.utc).timestamp() * 1000)
        return [
            {"instId": "BTC-USDT", "billId": "1", "ordId": "o1", "fillPx": "50000", "fillSz": "0.001",
             "fee": "-0.000001", "feeCcy": "BTC", "execType": "T", "tag": "Y1RAN01", "fillTime": str(t), "ts": str(t)},
            {"instId": "BTC-USDT", "billId": "2", "ordId": "o2", "fillPx": "51000", "fillSz": "0.001",
             "fee": "-0.051", "feeCcy": "USDT", "execType": "M", "tag": "", "fillTime": str(t + 86400000), "ts": str(t)},
            {"instId": "ETH-BTC", "billId": "3", "ordId": "o3", "fillPx": "0.03", "fillSz": "1",
             "fee": "-0.00003", "feeCcy": "BTC", "execType": "T", "tag": "", "fillTime": str(t), "ts": str(t)},
        ]

    def test_math(self):
        r = g.analyze(self.fills(), Decimal("100"), 30, code="Y1RAN01")
        self.assertEqual(r["fills"], 2); self.assertEqual(r["skipped_non_stable"], 1)
        self.assertEqual(r["turnover"], Decimal("101.00"))
        self.assertEqual(r["fees"], Decimal("0.1010"))          # 0.000001*50000 + 0.051
        self.assertEqual(r["fee_bps"], Decimal("10.0"))
        self.assertEqual(r["taker_ratio"], Decimal("0.50"))
        self.assertEqual(r["tagged_fills"], 1)
        self.assertEqual(r["annual_fee_pct_of_equity"], Decimal("1.2"))  # 0.101/100*365/30
        self.assertEqual(r["grade"], "绿")
        self.assertIn("绿灯", g.render_report(r))

    def test_grade_red_on_overtrading(self):
        r = {"orders_per_active_day": Decimal(12), "taker_ratio": Decimal(0)}
        self.assertEqual(g.grade(r), "红")

    def test_pagination(self):
        ex = FakeOKX(fills=[{"billId": str(i), "instId": "BTC-USDT", "ordId": str(i), "fillPx": "1", "fillSz": "1",
                             "fee": "0", "feeCcy": "USDT", "ts": "0"} for i in range(250)])
        with patch.object(client.requests, "get", side_effect=ex.get):
            out = g.fetch_fills(g.Creds("k", "s", "p", True), sleep=lambda s: None)
        self.assertEqual(len(out), 250)
        self.assertEqual([q.get("after") for _, q, _ in ex.gets], [None, "99", "199"])


class CredsTest(unittest.TestCase):
    def test_live_needs_confirm(self):
        env = {"OKX_PROFILE": "live", "OKX_API_KEY": "a", "OKX_SECRET_KEY": "b", "OKX_PASSPHRASE": "c"}
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(g.GuardError):
                g.Creds.from_env()
            self.assertFalse(g.Creds.from_env(confirm_live=True).simulated)

    def test_default_demo(self):
        with patch.dict(os.environ, {"OKX_API_KEY": "a", "OKX_SECRET_KEY": "b", "OKX_PASSPHRASE": "c"}, clear=True):
            self.assertTrue(g.Creds.from_env().simulated)


if __name__ == "__main__":
    unittest.main()
