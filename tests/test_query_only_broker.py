"""Query-only broker protocol tests.

1. readonly=False is rejected structurally (no config-based execution mode).
2. The connection flow is driven through guarded FAKE SDK contexts that fail
   if any unlock/order/cancel/modify member is accessed.
"""

import threading
import unittest

import pandas as pd

from core.broker_protocol import FORBIDDEN_SDK_MEMBERS
from core.connection_manager import MoomooConnection
from core.context_factory import QueryOnlyTradeContext

try:
    from moomoo import RET_OK, SecurityFirm, TrdEnv
except ImportError:  # pragma: no cover - SDK installed in locked env
    RET_OK = "RET_OK"
    SecurityFirm = None
    TrdEnv = None


class GuardedFakeContext:
    """Fake SDK context that records attribute access and hard-fails on
    forbidden (order/unlock) members."""

    def __init__(self, label, methods):
        self._label = label
        self._accessed = []
        for name, fn in methods.items():
            object.__setattr__(self, name, fn)

    def __getattribute__(self, name):
        if name.startswith("_"):
            return object.__getattribute__(self, name)
        if name in FORBIDDEN_SDK_MEMBERS:
            raise AssertionError(f"{self._label}.{name} called - query-only surface violated")
        object.__getattribute__(self, "_accessed").append(name)
        return object.__getattribute__(self, name)

    @property
    def accessed(self):
        return list(self._accessed)


def _empty_df(columns=("code",)):
    return pd.DataFrame(columns=list(columns))


def _build_fake_contexts():
    acc_df = pd.DataFrame([{"acc_id": "ACC1", "trd_env": TrdEnv.SIMULATE, "security_firm": "FUTUSECURITIES"}])
    price_df = pd.DataFrame([{"code": "US.AAPL", "last_price": 150.0}])
    acc_info_df = pd.DataFrame(
        [
            {
                "acc_id": "ACC1",
                "usd_assets": 100000.0,
                "us_cash": 50000.0,
                "total_assets": 100000.0,
                "usd_net_cash_power": 80000.0,
                "available_funds": 50000.0,
                "avl_withdrawal_cash": 50000.0,
                "initial_margin": 0.0,
                "maintenance_margin": 0.0,
                "frozen_cash": 0.0,
            }
        ]
    )
    group_df = pd.DataFrame([{"group_name": "My Watchlist", "stock_list": ["US.AAPL"]}])

    quote_methods = {
        "get_global_state": lambda: (RET_OK, "OK"),
        "get_market_snapshot": lambda symbols: (
            RET_OK,
            price_df if symbols and "OPT" not in str(symbols) else _empty_df(),
        ),
        "get_option_expiration_date": lambda code: (RET_OK, pd.DataFrame([{"expiration_date": "2026-09-18"}])),
        "get_option_chain": lambda **kw: (RET_OK, _empty_df()),
        "get_user_security_group": lambda **kw: (RET_OK, group_df),
        "get_user_security": lambda group_name: (RET_OK, group_df),
        "close": lambda: None,
    }
    trade_methods = {
        "get_acc_list": lambda: (RET_OK, acc_df),
        "accinfo_query": lambda **kw: (RET_OK, acc_info_df),
        "position_list_query": lambda **kw: (RET_OK, _empty_df()),
        "close": lambda: None,
    }
    return GuardedFakeContext("quote_ctx", quote_methods), GuardedFakeContext("trd_ctx", trade_methods)


class TestQueryOnlyTradeContext(unittest.TestCase):
    def test_forbidden_sdk_members_are_not_exposed(self):
        class RawContext:
            def get_acc_list(self):
                return RET_OK, None

            def accinfo_query(self, **kwargs):
                return RET_OK, None

            def position_list_query(self, **kwargs):
                return RET_OK, None

            def close(self):
                return None

            def place_order(self, **kwargs):
                raise AssertionError("order method must not be reachable")

        context = QueryOnlyTradeContext(RawContext())

        self.assertTrue(hasattr(context, "get_acc_list"))
        for member in FORBIDDEN_SDK_MEMBERS:
            self.assertFalse(hasattr(context, member))


class TestReadOnlyStructuralRejection(unittest.TestCase):
    def setUp(self):
        MoomooConnection._instances.clear()

    def test_readonly_false_is_rejected(self):
        with self.assertRaises(ValueError):
            MoomooConnection(readonly=False)


class TestQueryOnlySurface(unittest.TestCase):
    def setUp(self):
        MoomooConnection._instances.clear()
        # Reset class-level state that other tests may leave held/primed
        MoomooConnection._option_chain_gate = threading.BoundedSemaphore(1)
        MoomooConnection._option_chain_rate_limiter = None
        self.quote_fake, self.trd_fake = _build_fake_contexts()
        # Unique port so the singleton cache always creates a fresh instance
        # regardless of what other tests created.
        self._port = 41000 + (id(self) % 1000)

    def _make_connection(self):
        conn = MoomooConnection(
            host="127.0.0.1",
            port=self._port,
            readonly=True,
            portfolio_env="SIMULATE",
            security_firm="FUTUSECURITIES",
        )
        # Inject the guarded fakes directly (no real SDK contexts).
        conn.quote_ctx, conn.trd_ctx = self.quote_fake, self.trd_fake
        conn._connected = True
        return conn

    def test_wheel_flow_never_touches_order_members(self):
        conn = self._make_connection()

        # Exercise the query flow the wheel app uses daily.
        self.assertEqual(conn.get_connection_info()["connected"], True)
        self.assertEqual(conn.get_stock_price("AAPL"), 150.0)
        conn.get_option_expiration_dates("AAPL")
        conn.get_option_chain("AAPL", expiration="20260918", right="C")
        portfolio = conn.get_portfolio()
        self.assertIsNotNone(portfolio)
        self.assertEqual(portfolio["account_id"], "ACC1")
        conn.get_user_security_group()

        forbidden_touched = [
            name for name in FORBIDDEN_SDK_MEMBERS if name in self.quote_fake.accessed or name in self.trd_fake.accessed
        ]
        self.assertEqual(forbidden_touched, [], f"forbidden SDK members accessed: {forbidden_touched}")
        self.assertIn("get_market_snapshot", self.quote_fake.accessed)
        self.assertIn("position_list_query", self.trd_fake.accessed)


if __name__ == "__main__":
    unittest.main()
