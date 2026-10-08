# OpenD analytics capability probe

Probe run: 2026-09-30, against the local OpenD endpoint using the installed `moomoo-api` 10.9.6908 SDK. OpenD was reachable. The probe used the public sample symbol `US.AAPL`, requested bounded date ranges, and emitted only status and aggregate row/quota counts. It did not retain account IDs, broker rows, position values, or provider error text.

The OpenD startup log reports server version `10.2.6208` and an available update to `10.10.7008`. Moomoo's official changelog associates comprehensive IV/HV, upcoming-earnings, and dividend-calendar APIs with OpenD `10.8.6808` (2026-06-25). The four `Unknown protocol id` responses are strongly consistent with the running server being older than the documented feature release (inference from the observed version and changelog). Update OpenD, then rerun this probe before reconsidering the external earnings/ex-dividend providers or adopting any of these endpoints. [Official Moomoo OpenD changelog](https://openapi.moomoo.com/moomoo-api-doc/en/changelog/changelog.html).

The supplied number appears in OpenD logs as `uniCardNum`, not `accID`. The matching REAL account row reports `securityFirm=4`, which the installed SDK maps to `FUTUAU`. The first probe used `FUTUSECURITIES`, so the SDK filtered that REAL account out of the returned account list. Re-running with the log-indicated `FUTUAU` firm and the corresponding `accID` made the REAL account visible and all three account-specific read queries returned `RET_OK`. Identifiers and account data were used in memory for that run only; nothing was written to config or retained here. A REAL `position_list_query` returned 10 rows with a non-null `diluted_cost` column for all 10. Per owner decision O3, this confirms field availability only; it does not authorize using `diluted_cost` as basis evidence.

The probe is available at [`tools/probe_opend_analytics.py`](../tools/probe_opend_analytics.py). It is diagnostic tooling only; no application runtime or broker protocol code was changed.

## Results

| Candidate API | SDK signature | Probe result | Entitlement finding |
| --- | --- | --- | --- |
| `get_option_underlying_overview` | `(code_list, index_option_type='NORMAL')` | `unsupported_or_invalid_request` | OpenD returned `Unknown protocol id`. This is consistent with the running OpenD build not recognizing the SDK protocol; entitlement was not tested. |
| `get_option_underlying_his_volatility` | `(code, index_option_type='NORMAL', begin_time=None, end_time=None, page_req_key=None)` | `unsupported_or_invalid_request` | OpenD returned `Unknown protocol id`; entitlement was not tested. |
| `get_earnings_calendar` | `(market, sort_type=None, begin_date=None, end_date=None, filter_list=None)` | `unsupported_or_invalid_request` | OpenD returned `Unknown protocol id`; entitlement was not tested. |
| `get_dividend_calendar` | `(market, date, data_from=None, count=None)` | `unsupported_or_invalid_request` | OpenD returned `Unknown protocol id`; entitlement was not tested. |
| `get_rehab` | `(code)` | `ok`, 62 rows | Request succeeded for the sample security. Returned columns include historical `ex_div_date` and `per_cash_div`; this confirms corporate-action history access, not a forward dividend calendar. |
| `request_trading_days` | `(market=None, start=None, end=None, code=None)` | `ok`, 6 rows | Request succeeded for the US market date range. |
| `get_market_state` | `(code_list)` | `ok`, 1 row | Request succeeded for the sample security. |
| `request_history_kline` | `(code, start=None, end=None, ktype='K_DAY', autype='qfq', fields=[''], max_count=1000, page_req_key=None, extended_time=False, session='N/A')` | `ok`, 2 rows | Request succeeded for the sample security and bounded daily range. |
| `get_history_kl_quota` | `(get_detail=False)` | `ok`, 1 used / 299 remaining | Aggregate quota query succeeded. The probe emits only aggregate counts and suppresses per-code details. |
| `order_list_query` | `(order_id='', status_filter_list=[], code='', start='', end='', trd_env='REAL', acc_id=0, acc_index=0, refresh_cache=False, order_market='N/A')` | `ok`, 0 rows for the bounded sample query | REAL account-list match succeeded with the log-indicated firm. The query used the current date and sample symbol; zero rows only means no matching sample orders were returned. |
| `acctradinginfo_query` | `(order_type, code, price, order_id=None, adjust_limit=0, trd_env='REAL', acc_id=0, acc_index=0, session='N/A', jp_acc_type='JP_GENERAL', position_id=None)` | `ok`, 1 row | The bounded read-only sample request was accepted. Account values were deliberately not retained. |
| `get_acc_list` | `()` | `ok`; matching REAL account listed with `FUTUAU` filter | `FUTUSECURITIES` hid the REAL row because its log-reported security firm maps to `FUTUAU` in this SDK. Do not put `uniCardNum` or `cardNum` in the app's `account_id` setting. |
| `position_list_query` | `(code='', pl_ratio_min=None, pl_ratio_max=None, trd_env='REAL', acc_id=0, acc_index=0, refresh_cache=False)` | `ok`, 10 position rows; `diluted_cost` present and non-null on 10 rows | This establishes field availability only. Do not use `diluted_cost` as basis evidence without the explicit owner approval required by O3. Position values were not retained. |

Supplemental `query_subscription(is_all_conn=True)` succeeded and returned 8 rows. The probe intentionally does not print subscription rows because they can include account or subscription details; the aggregate count does not establish entitlement for any individual candidate endpoint.

The SDK documentation describes the quote APIs and their request shapes in the installed `moomoo/quote/open_quote_context.py`; the trade API signatures are in `moomoo/trade/open_trade_context.py`. The signatures above were inspected from the installed package, rather than inferred from the runtime wrapper.

## Interpretation and remaining checks

`ok` means OpenD returned `RET_OK` for the sample request. An empty successful response would still establish that the request was accepted, but the recorded successful calls here returned the counts shown above. For the four candidate analytics APIs, a sanitized in-memory summary of the SDK error was `Unknown protocol id`; the raw provider response was not retained. This points to protocol support/version compatibility and does not prove an entitlement denial. Do not adopt these APIs until they return `RET_OK` on the owner's running OpenD.

The following checks remain unverified:

- Whether an OpenD update makes the option overview, historical volatility, earnings calendar, and dividend calendar protocol IDs available; their current `Unknown protocol id` response leaves entitlement untested.
- Whether `diluted_cost` may be used as a basis source; field availability was confirmed, but owner approval under O3 is still required.
- Whether successful sample calls behave consistently across the full Moomoo watchlist and the intended production query sizes.

The app's local connection config was inspected without emitting its account ID; its `REAL` environment, `FUTUAU` security firm, and explicit `acc_id` match the log-derived account row. No config values were changed.

The probe contains no order placement, unlock, cancel, or modify calls. The three trade-context candidates are query APIs; their live calls require an explicitly supplied account ID and are passed through a wrapper that exposes only those query methods plus context close. The log-derived account identifier and all account data were used in memory only and were not written to this report or saved configuration.
