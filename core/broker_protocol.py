"""Query-only broker protocol.

The wheel app is structurally read-only. This module defines:

- the *only* broker operations the application may perform (query surface);
- the SDK members that are forbidden everywhere in runtime code;
- helpers used by the runtime and by tests to enforce the boundary.

Enforcement is structural, not configuration-based:

1. `MoomooConnection(readonly=False)` raises ``ValueError``; there is no
   supported "live/execution" configuration.
2. The application broker interface (``BrokerQuerySurface``) exposes query
   methods only.
3. ``tests/test_query_only_broker.py`` drives the connection through a
   guarded fake SDK context that fails if any forbidden member is accessed.
4. ``tests/test_no_execution_surface.py`` runs an AST/repository scan over
   the runtime source proving no forbidden member is ever called or imported.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

# SDK trade members that would grant order/unlock capability. Presence of any
# of these names in runtime code (outside tests) is a hard violation.
FORBIDDEN_SDK_MEMBERS: tuple[str, ...] = (
    "unlock_trade",
    "place_order",
    "modify_order",
    "cancel_order",
    "place_combo_order",
    "place_crypto_order",
    "cancel_crypto_order",
    "modify_crypto_order",
)

# SDK members that ARE part of the query surface (read-only account/market
# data). Used by the fake-context test to build a realistic surface.
QUERY_SDK_MEMBERS: tuple[str, ...] = (
    "get_acc_list",
    "get_global_state",
    "get_option_expiration_date",
    "get_market_snapshot",
    "get_capital_distribution",
    "get_capital_flow",
    "get_broker_queue",
    "request_history_kline",
    "get_cur_kline",
    "get_owner_plate",
    "get_option_chain",
    "accinfo_query",
    "position_list_query",
    "get_user_security_group",
    "get_user_security",
    "query_subscription",
    "get_option_volatility",
    "get_option_exercise_probability",
    "get_option_screen",
    "get_short_interest",
    "get_financials_earnings_price_move",
    "close",
)


@runtime_checkable
class BrokerQuerySurface(Protocol):
    """The complete application-level broker surface: queries only.

    Implementations (``core.connection_manager.MoomooConnection``) must never
    expose unlock/order/cancel/modify members.
    """

    def connect(self) -> bool: ...

    def get_connection_info(self) -> dict[str, Any]: ...

    def get_stock_price(self, symbol: str) -> float | None: ...

    def get_option_expiration_dates(self, symbol: str) -> list[Any]: ...

    def get_option_chain(self, symbol: str, expiration=None, right="C", target_strike=None, data_filter=None) -> Any: ...

    def get_portfolio(self) -> dict[str, Any] | None: ...

    def get_user_security_group(self, group_type=None) -> Any: ...

    def get_market_snapshot(self, symbols) -> Any: ...
