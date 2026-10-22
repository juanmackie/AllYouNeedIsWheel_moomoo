# Broker history capability (OpenD, read-only)

Findings from `tools/probe_broker_history.py`, which uses the app's structurally
read-only `MoomooConnection`. The probe prints aggregate counts and timings;
it does not print account identifiers, contract codes, order/deal identifiers,
or private fill rows.

## Latest measurement

Measured on 2026-10-11 against the configured live OpenD session. The probe
requested the maximum 90-day deal window and sampled fees for 20 of the 55
orders returned.

| Question | Observation |
|---|---|
| OpenD reachable | Yes; account identity resolved (identity value omitted) |
| Deal history | 59 rows, 2026-07-13 through 2026-10-06; no fee field on deal rows |
| Deal query time | 0.184 seconds for one 90-day query |
| Fee history | 20 of 20 sampled orders returned fee rows |
| Fee query time | 0.250 seconds for one 20-order query |
| Local trade-query limiter | 45 requests per 60 seconds; burst threshold 20 per 10 seconds |
| Expired short contracts | 18 had sell fills; 15 were bought back before expiry, 2 had a zero-price buy on/after expiry, 1 had another buy on/after expiry; none remained net short |
| Zero-price option buys | 3; each had a stock fill within 2 seconds at the strike, with matching side and share count |

The three zero-price option and stock matches are evidence consistent with
assignment deliveries. They do not establish all assignment encodings. The
two zero-price closes on/after expiry were both paired with matching stock
fills, so the sample contains no confirmed expired-worthless contract. This
run cannot determine whether such an option is omitted, reported as a
zero-price close without shares, or shown another way. The SDK call timings
are one sample per query type; the local limiter is an application guardrail,
not a measurement of the broker's actual trade-API quota. G1 therefore
remains **unmeasured** for worthless expiry behavior and the authoritative
API quota.

## Measurement 2026-10-23 (windowed history, lifecycle encoding, cash-flow quota)

A read-only script used the same `MoomooConnection` against the live OpenD
session. It queried consecutive 89-day windows with explicit `start` and `end`
from 2025-10-01 to 2026-10-23.

| Question | Observation |
|---|---|
| History older than 90 days | Reachable. The windows returned 0, 1, 34, 55, and 18 deal rows, 108 unique deal ids in total, from 2026-02-25. The 90-day limit is per query, not per account. |
| Stored fills vs broker | All 73 stored fills matched broker deal ids, with no field differences. The 35 older deals were missing only because earlier pulls were limited to 90 days. |
| Fees for old orders | `order_fee_query` returned fees for all 33 older orders that were queried. |
| Expired short options | Three expired short options (two puts, one call) appeared as a zero-price BUY close with no share fill. |
| Expired long options | Two expired long option positions appeared as a zero-price SELL close. |
| Assignments | Zero-price option close plus a share fill at the strike (as in the 2026-10-11 sample). |
| Cash-flow quota | `get_acc_cash_flow` rejects more than 20 requests per 30 seconds ("Get Cash Flow Summary is too frequent"). At 1.6 s spacing, 170 weekday queries all succeeded. |

This is lifecycle evidence for G1. It is not yet owner-confirmed, so the stored
G1 capability is unchanged and expiry inference stays disabled. The account P&L
read model (`core/account_pnl.py`) needs no inference: it reconciles each
contract by strict quantity equality, so a missing close record shows as an
unknown row, never as profit.

## Implications

1. One history request covers at most 90 days, but consecutive windows reach
   older history. `FillsService.ingest_history_fills` backfills once from
   `broker_history_start` and then pulls incrementally, so downtime longer than
   90 days no longer leaves a permanent gap.
2. Deal rows do not include fees. Fees require `order_fee_query`; ingestion
   allocates returned order fees over that order's fills and keeps unknown fees
   unknown.
3. Do not infer expired-worthless status from an absent deal. Expiry handling
   stays disabled until a later probe has positive, owner-confirmed lifecycle
   evidence and bounded query behavior. Fills are pulled on every refresh (owner
   decision 2026-10-17). The pull is query-only and idempotent. Outcomes label an
   open contract past expiry `unresolved_after_expiry` unless measured G1 evidence
   and a successful fill pull covering the expiry both exist.
4. The current campaign implementation defaults to Moomoo average cost and
   labels the capability reason while G1 is unmeasured.

## Re-running

```powershell
uv run python tools/probe_broker_history.py
```

The probe uses one history query and at most one fee query over 20 order IDs.
It is query-only and emits aggregate measurements only.
