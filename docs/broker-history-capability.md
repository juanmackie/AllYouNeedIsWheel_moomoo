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

## Implications

1. A history request covers at most 90 days. Incremental pulls can preserve
   continuity only while the app successfully refreshes inside that window.
2. Deal rows do not include fees. Fees require `order_fee_query`; ingestion
   allocates returned order fees over that order's fills and keeps unknown fees
   unknown.
3. Do not infer expired-worthless status from an absent deal. Expiry handling
   and refresh-time ingestion remain disabled until a later probe has positive,
   owner-confirmed lifecycle evidence and bounded query behavior.
4. The current campaign implementation defaults to Moomoo average cost and
   labels the capability reason while G1 is unmeasured.

## Re-running

```powershell
uv run python tools/probe_broker_history.py
```

The probe uses one history query and at most one fee query over 20 order IDs.
It is query-only and emits aggregate measurements only.
