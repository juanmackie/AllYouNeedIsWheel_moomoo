# Broker history capability (OpenD, read-only)

What the Moomoo OpenD API actually exposes for outcome attribution, and what that
implies. Measured with `tools/probe_broker_history.py` (query-only — it uses the
app's structurally read-only `MoomooConnection`, the same surface
`core/broker_protocol.py` and `tests/test_no_execution_surface.py` enforce).

Observed on 2026-09-20 against a live REAL account (`FUTUAU`), 89-day request:

| Question | Answer |
|---|---|
| OpenD reachable / account resolvable | Yes (loopback, REAL, opaque account id resolved) |
| Historical fills (`history_deal_list_query`) | **45 fills**, 2026-06-23 → 2026-09-17 |
| Order history (`history_order_list_query`) | 94 orders over the same window |
| Fees (`order_fee_query`) | Available per order, itemised (Commission, Platform, Options Regulatory, OCC, …); **not on the deal row** |
| Cash movements (`get_acc_cash_flow`) | 1 row (depth to 2026-09-02); one clearing date per query |
| Positions (`position_list_query`) | 9 |

## Implications for attribution

1. **A single query covers at most 90 days.** Older trades cannot be pulled by
   the same call, so realized P&L is only ever attributable inside that window.
   The owner's own broker export spanned April → September: anything before the
   window is permanently unattributable through the API.
2. **Fills carry no fees.** Premium P&L is exact from the deal row; net P&L needs
   one `order_fee_query` per order, allocated across that order's fills (the app
   allocates proportionally to gross fill value, and leaves fees `NULL` — never
   `0.0` — when a fee row is missing).
3. **Deal rows are the join key to recommendations.** `code` decodes to
   underlying/expiry/type/strike, which is the same contract identity the
   recommendation signals use; nothing else in the payload names the contract.
4. **Fills alone never say which recommendation they came from.** The window
   above contained 45 fills and zero exact matches against 208 recorded
   recommendations — hence the owner-recorded "taken" link
   (`api/services/taken_links.py`) as the explicit, non-inferred attribution path.

## Re-running

```
.venv/Scripts/python tools/probe_broker_history.py
```

It prints the same counts as JSON. A missing/empty result is a finding, never a
fabricated row.
