# Research: Wheel-scanner decision workflow and option analytics benchmarks

## Summary

The strongest compatible benchmark is not a larger scanner: it is a faster, more explicit **qualify → compare → copy to Moomoo** workflow. Wheel Strategy Options demonstrates useful presentation patterns—yield beside downside cushion, exact DTE, IV context, earnings exclusion, liquidity cleanup, and saved setups—while PVE’s flow/dark-pool/congress/OSINT product is mostly incompatible with this repository’s watchlist-only, Moomoo-actionable boundary.

The repository already implements much of the right foundation, including premium velocity, breakeven buffer, source/freshness labels, immutable presets, and Moomoo-only copy gating. The highest-priority work is to fix a copied-ticket quantity defect, make heuristic analytics less authoritative, tighten or empirically validate liquidity gates, and use newer Moomoo volatility/option-quote capabilities selectively for shortlisted contracts.

> This is product/technical research, not investment advice. Competitor claims below describe their products; they are not evidence of strategy performance.

## Findings

### 1. Verified competitor capabilities

1. **Wheel Strategy Options uses a three-step manual workflow compatible with this project.** Its CSP and covered-call pages explicitly describe “Filter & Analyze → Execute in your brokerage → Save your filters,” preserving human execution. The directly retrieved pages show compact result columns for score, symbol, stock price, strike, premium, delta, cushion, yield, expiration/DTE, and IV Rank. [CSP page](https://wheelstrategyoptions.com/about-cash-secured-put-screener) · [Covered-call page](https://wheelstrategyoptions.com/about-covered-call-screener)

2. **Its best decision pattern is paired return-and-risk context, not its broad-market scale.** The product explicitly warns that ranking yield alone can hide a “cushion trap” and promotes viewing yield with strike/discount cushion, DTE, IV rank, and earnings risk. It also advertises volume/open-interest/spread “liquidity cleanup,” beginner/advanced disclosure, and saved screener configurations. These patterns are compatible when applied only to this app’s canonical watchlist union. [CSP page](https://wheelstrategyoptions.com/about-cash-secured-put-screener) · [Screener guide](https://wheelstrategyoptions.com/learn/screener-guide)

3. **The Wheel Strategy Options marketing preview is not market-data validation.** The retrieved preview shows altered-looking symbols and generated dates; therefore its row values, claimed contract counts, speed, user counts, scores, and performance-oriented testimonials should not be used as evidence for formulas or thresholds. Only the observable workflow/column design is a reliable benchmark from the public page.

4. **PVE is verified as a market-intelligence platform, not a wheel decision tool.** Its live HTML/JSON-LD describes real-time options flow, sweep/golden-sweep/block detection, dark-pool activity, congress trades, insider filings, GEX, ticker intelligence, and OSINT-driven analysis. Search-indexed product pages additionally describe filtering flow by ticker, expiration, premium, and trade type. [PVE](https://pve.trade/) · [Options flow](https://pve.trade/option-flow) · [Ticker hub](https://pve.trade/ticker) · [Docs](https://pve.trade/docs)

5. **PVE’s actual signed-in interaction quality was not verified.** The site is JavaScript-rendered and readable extraction could retrieve only its HTML shell/structured metadata; no authenticated dashboard behavior, data accuracy, latency, accessibility, or decision quality was observed. Its capabilities above are verified as published product claims, not independently validated behavior.

### 2. Authoritative analytics and risk evidence

6. **Assignment is possible on any business day for American-style short options.** The Options Industry Council states that assignment can occur while a short position remains open; likelihood generally rises as an option goes deeper ITM and approaches expiration. It also notes short-call risk around ex-dividend dates and OCC’s exercise-by-exception procedure for equity options at least $0.01 ITM, subject to contrary instructions and broker policy. [OIC assignment FAQ](https://www.optionseducation.org/referencelibrary/faq/options-assignment) · [OCC Options Disclosure Document](https://www.theocc.com/getmedia/a151a9ae-d784-4a15-bdeb-23a029f50b70/riskstoc.pdf)

7. **Delta is useful probability context, not a guaranteed assignment probability.** OIC explicitly teaches multiple interpretations of delta, including use as a probability metric. The app should call `1 - |delta|` an approximation/model input, not a factual probability of profit or assignment. [OIC delta webinar](https://www.optionseducation.org/videolibrary/probability-i-delta-as-a-probability-metric)

8. **Moomoo’s current OpenAPI can support richer Moomoo-only analytics.** Official documentation exposes option-chain Greeks/IV/OI/volume filters, expiration-distance data, option volatility history (IV, HV, IV−HV premium, average IV, status), and option quote analysis fields including mark/mid, intrinsic/time value, breakeven, distance to breakeven, probability of profit, seller ROI, DTE, multiplier, and Greeks. This makes volatility context and model-result comparison possible without allowing an external provider to create a pick. [Option chain](https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-option-chain.html) · [Option volatility](https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-option-volatility.html) · [Option quote](https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-option-quote.html) · [Real-time quote](https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-stock-quote.html)

9. **New Moomoo endpoints need capability probing and quota-aware use.** The retrieved docs span versions 10.2–10.10, while this repository’s dependency floor is older and unpinned above a minimum. `get_option_quote` is documented around a combo-leg list rather than this project’s current single-contract path. Do not assume local OpenD/account entitlements or signatures; probe support and fetch expensive volatility history only for the already-qualified shortlist.

### 3. Concrete repository findings and prioritized opportunities

1. **P0 / Critical — copied tickets can use the maximum affordable quantity instead of the recommended quantity.** `frontend/static/js/dashboard/top-recommendations.js` builds `qty` as `rec.max_contracts || rec.recommended_contracts`, so any positive maximum wins even when the scorer recommends fewer contracts. This can turn a conservative signal into a maximum-size manual ticket.
   - **Fix outcome:** prefer `recommended_contracts`; if missing/zero, block copying or require explicit quantity review rather than silently using `max_contracts` or `1`.
   - **Acceptance:** a fixture with `max_contracts=5` and `recommended_contracts=1` copies `x1`; missing recommendation disables copy with a reason; existing ready/fresh/Moomoo gates still apply.

2. **P1 / High — “expected value” is presented more strongly than its model supports.** `SCORING.md` and `core/wheel_decision.py` use `PoP = 1 - |delta|`, a fixed 10% CSP loss estimate, and treat covered-call expected value as the premium retained. These are scoring heuristics, not a statistically calibrated expected-value distribution.
   - **Fix outcome:** rename the subscore to “scenario EV heuristic” (with assumptions adjacent), or replace it only after a documented/calibrated model exists. Show Moomoo’s PoP/ROI as a separately sourced broker model when available; never silently mix the two.
   - **Acceptance:** no UI/API label implies a guaranteed or calibrated probability; formulas and assumptions match `SCORING.md`; tests pin model/source labels and unknown behavior.

3. **P1 / High — liquidity hard gates appear too permissive for a premium-selling shortlist.** `core/presets.py` permits maximum mid-relative spreads of 45%, 60%, and 70%, with minimum OI of 25, 10, and 5. `core/wheel_decision.py` then treats contracts under those ceilings as tradable. This can allow headline premium velocity to be dominated by non-executable mid prices.
   - **Fix outcome:** first analyze observed Moomoo watchlist distributions and fillability proxies, then set evidence-based spread/OI/volume gates by preset. Rank velocity using an executable/conservative sell-credit estimate (for example bid or a documented bid-to-mid haircut), while still displaying mid.
   - **Acceptance:** wide/one-sided examples never become copyable; velocity ranking is stable against an inflated ask; diagnostic rejections explain spread dollars and percent; thresholds are regression-tested.

4. **P1 / High — add an explicit assignment-risk strip to covered-call/roll decisions.** Current recommendation cards in `frontend/static/js/dashboard/top-recommendations.js` emphasize earnings, IV, buffer, and velocity, but authoritative risk guidance also requires expiration/ITM and ex-dividend awareness for calls.
   - **Fix outcome:** add plain-language “assignment can occur any business day” context, with elevated warnings for ITM/near-expiry and ex-dividend exposure when Moomoo-sourced or evidence-gated data is available. Unknown dividend timing must remain unknown, not “safe.”
   - **Acceptance:** test scenarios cover OTM, ITM near expiry, and ITM before ex-dividend; warnings never predict assignment certainty; copy gating follows the project’s source policy.

5. **P2 / Medium — selectively add Moomoo IV-vs-HV context for the top three, not the whole watchlist.** The competitor’s IV-rank pattern is useful, and Moomoo’s official `get_option_volatility` endpoint can provide IV, HV, volatility premium, average IV, and status without creating external actionable picks.
   - **Likely paths:** broker protocol/adapter under `core/`, shortlist orchestration in `api/services/recommendations.py`, run snapshot model in `core/run_model.py`, card rendering in `frontend/static/js/dashboard/top-recommendations.js`.
   - **Acceptance:** unsupported/unauthorized/no-data responses degrade to `unknown`; calls occur only after qualification and behind cache/rate limits; an old OpenD version still completes a run; external data cannot promote a candidate.

6. **P2 / Medium — turn each winner card into a compact decision table.** Existing cards already show velocity, annualized return, buffer, expected move, delta, IV, warnings, provenance, and “why this pick.” Reuse those values instead of adding a second scanner UI.
   - **Fix outcome:** align four questions in one scan path: **income speed** (premium/day and conservative executable credit), **assignment/call-away trade-off** (delta approximation, cushion/breakeven, cost basis), **execution quality** (bid/ask dollars and %, OI/volume), and **event/data risk** (earnings/dividend/quote age/source).
   - **Acceptance:** an operator can compare the top three without opening details; definitions are available progressively; the primary visual rank remains premium velocity within risk tier.

7. **P2 / Medium — add “why qualified / why not” preset explanations, not editable filter sprawl.** Wheel Strategy Options’ best teaching feature is mapping every filter/preset to a reason. This repository’s presets are intentionally immutable and should stay that way.
   - **Likely paths:** `core/presets.py`, preset API response, dashboard partials, `frontend/static/js/dashboard/top-recommendations.js`.
   - **Acceptance:** each winner lists passed hard gates and its strongest trade-off; each rejection uses stable reason codes and plain language; effective preset values remain read-only.

8. **P2 / Medium — reconcile public scoring documentation before exposing more analytics.** `SCORING.md` still states that risk-adjusted composite score is the ranking philosophy and contains removed Catalyst Watch/social, VIX, macro, and yfinance language. That conflicts with the root contract that premium velocity ranks, the watchlist is the only universe, macro/social discovery is out of scope, and external data cannot create an actionable pick.
   - **Fix outcome:** rewrite only the stale contract sections and map each documented formula to current implementation/tests.
   - **Acceptance:** README, SCORING, presets, API payloads, card labels, and regression tests agree on qualification vs ranking, sources, and removed scope.

9. **P3 / Optional — retain PVE-like context only as diagnostic evidence.** A per-ticker context drawer could summarize already-available, permitted evidence for a shortlisted watchlist symbol, but options flow, dark pools, congress trades, insider alerts, GEX, OSINT sentiment, or social discovery must not create, qualify, rank, or unlock a pick.
   - **Acceptance:** disabling all overlays leaves picks/ranking unchanged; incomplete overlays say `unknown`; no new broad-market scan exists; no copy action becomes available because of external context.

### 4. Ideas to reject

- **Reject broad-market “2M+ contract” discovery.** It violates the complete canonical watchlist-union and free-tier OpenD quota design.
- **Reject PVE’s options-flow, dark-pool, congress, insider, prediction-market, and OSINT feeds as ranking inputs.** They are not Moomoo account/quote truth and would expand scope and operational dependencies.
- **Reject autonomous broker execution or order-prefill submission.** Preserve manual copy-to-ticket and structural read-only enforcement.
- **Reject editable 25+ filter panels.** Versioned immutable presets plus transparent effective values are safer and faster for this local daily workflow.
- **Reject “high IV means good” or “high delta means X% assignment” shortcuts.** IV can reflect binary event risk, and delta is only a model-dependent approximation.
- **Reject competitor score formulas and marketing thresholds.** Public pages do not disclose validated calibration, fills, fees, or complete methodology.

## Sources

### Kept

- [Wheel Strategy Options — CSP screener](https://wheelstrategyoptions.com/about-cash-secured-put-screener) — directly retrieved workflow, displayed analytics, and product claims; no stable publication date visible.
- [Wheel Strategy Options — covered-call screener](https://wheelstrategyoptions.com/about-covered-call-screener) — directly retrieved covered-call workflow and comparison columns; no stable publication date visible.
- [Wheel Strategy Options — screener guide](https://wheelstrategyoptions.com/learn/screener-guide) — product’s own documentation of filters/presets; no stable publication date visible.
- [PVE](https://pve.trade/) — directly retrieved HTML/JSON-LD describing the current product and plans; JavaScript UI itself was not observed.
- [PVE options flow](https://pve.trade/option-flow), [ticker hub](https://pve.trade/ticker), and [docs](https://pve.trade/docs) — indexed first-party pages used only to verify published capability claims.
- [OIC Options Assignment FAQ](https://www.optionseducation.org/referencelibrary/faq/options-assignment) — authoritative assignment and exercise-by-exception behavior.
- [OCC Characteristics and Risks of Standardized Options](https://www.theocc.com/getmedia/a151a9ae-d784-4a15-bdeb-23a029f50b70/riskstoc.pdf) — primary risk disclosure document.
- [OIC Delta as a Probability Metric](https://www.optionseducation.org/videolibrary/probability-i-delta-as-a-probability-metric) — authoritative support for qualified probability language.
- [Moomoo option-chain documentation](https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-option-chain.html) — official fields and chain behavior (retrieved docs labeled v10.2).
- [Moomoo option-volatility documentation](https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-option-volatility.html) — official IV/HV/volatility-premium history (retrieved docs labeled v10.8).
- [Moomoo option-quote documentation](https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-option-quote.html) — official analysis fields and combo-leg signature; version/entitlement must be probed locally.
- [Moomoo real-time quote documentation](https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-stock-quote.html) — official quote, OI, IV, and Greeks fields (search result labeled v10.10).

### Dropped or constrained

- Competitor testimonials, subscriber counts, “time to first idea,” subscription payback, and preview returns — marketing claims without independent validation.
- Competitor educational threshold claims such as a universal 30–45 DTE “sweet spot” or named safe delta ranges — useful hypotheses, not primary evidence or universally valid rules.
- Search snippets from blogs, Reddit, Investopedia, and vendor comparison pages — redundant or weaker than OCC/OIC/Moomoo primary sources.
- PVE signed-in dashboard behavior — inaccessible to the available readable fetch; capability claims are not treated as observed UX/data quality.

## Gaps

- PVE’s rendered/authenticated interface, real data accuracy, latency, keyboard behavior, and responsive states were not independently tested.
- Local OpenD version, market-data entitlements, and support for `get_option_volatility` / `get_option_quote` were not probed in this research task.
- No empirical Moomoo quote sample was analyzed, so recommended spread/OI thresholds should be calibrated from real watchlist runs rather than copied from a competitor.
- Taxes, commissions, assignment fees, and the user’s jurisdiction/broker schedule were outside this benchmark; any future “net return” metric must incorporate the user’s actual all-in costs.

```acceptance-report
{
  "criteriaSatisfied": [
    {
      "id": "criterion-1",
      "status": "satisfied",
      "evidence": "Prioritized findings include severities and concrete repository paths such as frontend/static/js/dashboard/top-recommendations.js, core/presets.py, core/wheel_decision.py, api/services/recommendations.py, core/run_model.py, and SCORING.md."
    }
  ],
  "changedFiles": [
    "research.md"
  ],
  "testsAddedOrUpdated": [],
  "commandsRun": [
    {
      "command": "web_search: competitor, OCC/OIC, and Moomoo documentation queries",
      "result": "passed",
      "summary": "Retrieved first-party Wheel Strategy Options, indexed PVE, OCC/OIC, and Moomoo sources; one configured-provider query failed and was retried with explicit providers."
    },
    {
      "command": "fetch_content: first-party competitor and official documentation pages",
      "result": "passed",
      "summary": "Wheel and Moomoo/OIC content was retrieved; PVE readable extraction was JS-blocked, so raw HTML/JSON-LD and indexed first-party pages were used with limitations stated."
    },
    {
      "command": "read: SCORING.md, api/services/recommendations.py, core/presets.py, core/wheel_decision.py, frontend/templates/dashboard.html, frontend/static/js/dashboard/top-recommendations.js",
      "result": "passed",
      "summary": "Mapped benchmark opportunities to existing implementation and found the ticket-quantity, heuristic-labeling, liquidity-threshold, and documentation-drift issues."
    }
  ],
  "validationOutput": [
    "Verified competitor claims are explicitly separated from inferred or unobserved behavior.",
    "Every external load-bearing claim links to a source opened or retrieved during this run.",
    "Recommendations preserve signals-only, structurally read-only, Moomoo-actionable, watchlist-only, premium-velocity-first contracts."
  ],
  "residualRisks": [
    "PVE authenticated UX/data behavior was not observed.",
    "Local OpenD endpoint support and entitlements remain unprobed.",
    "Liquidity threshold changes require empirical Moomoo quote analysis before implementation."
  ],
  "noStagedFiles": true,
  "diffSummary": "Added research.md only; no application code or tests changed and no staging command was used.",
  "reviewFindings": [
    "critical: frontend/static/js/dashboard/top-recommendations.js - copied ticket quantity prefers max_contracts over recommended_contracts.",
    "high: core/wheel_decision.py and SCORING.md - expected-value/probability labels overstate fixed heuristics.",
    "high: core/presets.py - 45% to 70% spread ceilings can admit weakly executable mid-price signals.",
    "medium: SCORING.md - documented ranking/source/feature contracts retain removed scope and conflict with current premium-velocity-first policy."
  ],
  "manualNotes": "No git-index inspection tool was used; no staging operation was performed by this agent."
}
```
