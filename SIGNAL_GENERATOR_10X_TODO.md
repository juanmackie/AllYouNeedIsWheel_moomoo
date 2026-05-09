# Signal Generator 10x TODO

Review date: 2026-05-06

Scope: polish the existing covered call and cash-secured put signal generator. Do not add new strategy types or broad new product features. The goal is to make current signals more trusted, explainable, safer to act on, and faster to validate.

## Current Signal Flow

1. Portfolio and cash context are built in `api/services/portfolio_context.py`.
2. Watchlist candidates come from static/dynamic watchlists in `api/services/watchlist_manager.py`.
3. Option chains and fallback data are loaded by `api/services/options_data.py`.
4. Top recommendations are assembled by `api/services/recommendations.py`.
5. Candidate scoring runs through `core/wheel_decision.py` and pure helpers in `core/scoring_factors.py`.
6. Dashboard cards and tables are rendered by `frontend/static/js/dashboard/top-recommendations.js` and the `options-table-*` modules.

Primary system goal: turn noisy option-chain data into a short, defensible list of wheel trades that the user can review, stage, and execute without hidden assumptions.

## Systemic Review Findings

### P0 Findings

- Test validation is coupled to broker SDK import side effects. Importing lightweight modules can import `moomoo`, which attempts to write logs under AppData during test collection. A signal generator that cannot be tested without OpenD side effects will keep producing whack-a-mole regressions.
- `core/greeks.py` imports `scipy.stats.norm`, but `scipy` is not listed in `requirements.txt`. If this is only available transitively, the Greeks fallback is fragile.
- Signal math is shown with too much precision for the current heuristic model. Expected value currently uses delta-derived POP and fixed loss estimates, not a calibrated outcome model.
- Put annualized return is computed against `stock_price * 100` in the shared formula, while the actual secured capital is `strike * 100`. This can skew put yield and ranking, especially for farther OTM puts.
- Implied volatility is stored as a decimal but some UI labels show it as a percent with no conversion. A value like `0.30` can render as `0.30%` instead of `30%`.
- Top recommendation "Execute now" creates and executes an order in one path without a rich pre-flight confirmation showing price, quantity, buying power impact, source freshness, and warnings.

### P1 Findings

- Data provenance is not first-class. Candidates may combine Moomoo price data, yfinance option fallback data, computed Black-Scholes Greeks, cached IV rank, FRED macro data, and TradingView watchlist inputs, but the UI does not clearly label which pieces came from where.
- Scores are ranked but not calibrated. A score of 85 does not yet mean "historically strong" or "expected to outperform," only "high under current weights."
- Rationale text explains factors, but not enough about what would disqualify the trade or what changed since the prior refresh.
- Covered call quantity defaults can use all available 100-share lots. That may be mechanically valid but is not necessarily the best default risk posture.
- The one-recommendation-per-ticker diversity rule improves variety, but it can hide the best call/put pair for the same high-quality ticker without explaining that tradeoff.
- Existing short puts are counted for reservation in multiple ways across portfolio context and scoring. Cash reservation must be single-source and auditable.

### P2 Findings

- Refresh flow is expensive and uneven. Recommendations, options table, account summary, macro, VIX, technical regime, positions, and orders can all refresh separately with different stale/error states.
- UI uses several action labels with different risk implications: Add, Stage order, Execute now, Add All, Sell. The distinction between staging and sending live orders needs to be unmistakable.
- Debug `console.log` calls remain in production JS. This adds noise while testing real trading flows.
- `StateModel` exists but is only partly adopted. Loading, empty, stale, and error states should look and behave the same across all signal surfaces.

## 10x Principles For This TODO

- Trust beats quantity. Fewer, better-explained signals are more valuable than more rows.
- The score is not the product; the decision is. Every score should expose why it passed, what could make it fail, and what the user risks.
- Broker data beats fallback data for execution decisions. Fallback data can help discovery, but must be visibly marked as lower confidence.
- Staging is safe by default. Execution requires explicit confirmation and should never feel like a convenience shortcut.
- Validation must be boring. Unit tests, import tests, smoke tests, and UI checks should run without OpenD, live credentials, or side effects.

## Phase 0: Validation And Trust Foundation

### TODO 0.1: Make tests independent from Moomoo import side effects

- Remove eager `MoomooConnection` import from `core/__init__.py`, or make it lazy.
- Ensure importing `core.rate_limiter`, `core.wheel_decision`, and `core.greeks` does not initialize the Moomoo SDK.
- Add an import-side-effect test that imports core utility modules with no OpenD, no writable AppData log path, and no network.
- Acceptance criteria:
  - `python -m pytest tests/test_import_side_effects.py -q` passes.
  - `python -m pytest tests/test_wheel_decision.py -q` does not touch Moomoo logs.

### TODO 0.2: Declare direct math dependencies

- Add `scipy` to `requirements.txt` if `core/greeks.py` remains scipy-based.
- Alternatively replace scipy usage with a local normal CDF/PDF helper only if accuracy is verified.
- Acceptance criteria:
  - Fresh venv install can import `core.greeks`.
  - CI fails if `core.greeks` dependencies are missing.

### TODO 0.3: Add score regression fixtures

- Create fixture chains for representative symbols:
  - Covered call with owned shares and clean liquidity.
  - Cash-secured put with enough cash and clean liquidity.
  - Low IV environment.
  - High IV environment.
  - Wide spread.
  - Earnings today.
  - Missing Greeks, computed Greeks fallback.
  - yfinance fallback data.
- Acceptance criteria:
  - Tests assert pass/fail, rank order, warnings, and data provenance.
  - Score changes require intentional fixture updates.

### TODO 0.4: Add encoding and display sanity checks

- Add a check for mojibake/replacement characters in Markdown, templates, JS, and CSS.
- Add UI unit/smoke checks for percent formatting:
  - IV decimal `0.30` displays as `30.0%`.
  - IV rank `70` displays as `70%`.
  - Delta displays consistently as signed Greek and absolute probability where appropriate.
- Acceptance criteria:
  - No `ð`, `â`, `Ã`, or replacement characters in user-facing source files.

## Phase 1: Scoring Correctness And Calibration

### TODO 1.1: Split scoring into hard filters, risk flags, and ranking score

- Keep hard filters for impossible or unsafe trades:
  - No stock price.
  - Expired option.
  - No meaningful premium.
  - Spread above hard threshold.
  - Covered call without enough uncovered shares.
  - CSP without enough available secured cash.
  - Earnings today if configured as a blocker.
- Keep warnings for tradable-but-risky cases:
  - Wide but acceptable spread.
  - Low open interest.
  - Low IV.
  - Near earnings.
  - Macro headwinds.
  - Fallback data source.
- Keep ranking score only for comparing candidates that passed filters.
- Acceptance criteria:
  - API returns `hard_blockers`, `warnings`, and `score_details` separately.
  - UI never hides why a contract was excluded when a user asks to inspect a ticker.

### TODO 1.2: Fix put return denominator

- For cash-secured puts, compute annualized return using `premium / cash_required`, where `cash_required = strike * 100 * contracts`.
- Preserve any existing field names only if needed for compatibility, but add explicit names:
  - `return_on_underlying`
  - `return_on_secured_cash`
  - `annualized_return`
- Acceptance criteria:
  - PUT tests prove annualized return changes with strike-based collateral.
  - UI labels say "Return on secured cash" for puts.

### TODO 1.3: Reframe expected value as heuristic until calibrated

- Rename or label current EV as `heuristic_expected_value`.
- Add score details explaining assumptions:
  - POP source: absolute delta.
  - Loss estimate method.
  - No historical assignment calibration yet.
- Do not show EV as a precise forecast.
- Acceptance criteria:
  - Rationale text says "heuristic EV" or similar.
  - Tests verify warnings/rationale include assumption disclosure.

### TODO 1.4: Normalize Greek and IV units at boundaries

- Standardize internal units:
  - IV decimal internally, percent only at display boundary.
  - IV rank 0-1 internally or 0-100 internally, but not mixed.
  - Delta signed internally, absolute value for probability-like copy.
- Add helper functions for serialization:
  - `serialize_iv_decimal`
  - `serialize_iv_rank_percent`
  - `serialize_delta_display`
- Acceptance criteria:
  - API schema documents units.
  - UI has no ad hoc `.toFixed(2) + '%'` for decimal IV.

### TODO 1.5: Calibrate score weights with fixture scenarios

- Define expected ranking behavior before tuning:
  - A narrow spread beats a slightly higher yield with poor liquidity.
  - Earnings today should never be top ranked unless explicitly allowed.
  - Low IV should suppress premium-selling signals.
  - A slightly lower return with much better breakeven buffer should beat a thin buffer.
  - Covered call below cost basis should be penalized heavily or blocked based on setting.
- Acceptance criteria:
  - Golden tests cover each ranking rule.
  - Weight changes are reviewed against all fixtures.

### TODO 1.6: Add confidence score separate from trade score

- Confidence should reflect data quality, not attractiveness:
  - Broker data present.
  - Bid/ask present.
  - Greeks broker-provided vs computed.
  - IV rank sample size enough.
  - Earnings date freshness.
  - Quote age.
- Acceptance criteria:
  - A high-return yfinance fallback candidate can have high trade score but lower confidence.
  - UI sorts/renders score and confidence distinctly.

## Phase 2: Data Quality And Provenance

### TODO 2.1: Add source metadata to every candidate

- Track per field or per candidate:
  - `price_source`: Moomoo, portfolio fallback, yfinance.
  - `chain_source`: Moomoo, yfinance.
  - `greeks_source`: broker, Black-Scholes computed, missing.
  - `iv_source`: broker, yfinance, historical cache.
  - `earnings_source`: provider/cache/manual.
  - `macro_source`: FRED/cache/disabled.
  - `quote_timestamp` and `generated_at`.
- Acceptance criteria:
  - Top recommendation cards show data source and freshness.
  - Orders staged from fallback data include a visible verification warning.

### TODO 2.2: Make yfinance fallback discovery-only by default

- Keep yfinance candidates useful for scanning.
- Require broker confirmation before execution:
  - Refresh exact contract from Moomoo before execute.
  - If broker quote unavailable, block "Execute now" and allow staging only with warning.
- Acceptance criteria:
  - Execute path refuses fallback-only quote data.
  - Staged order stores data source metadata.

### TODO 2.3: Centralize cash reservation

- Use one cash reservation function for:
  - Dashboard cash reserve bar.
  - PUT hard filters.
  - Recommendation sizing.
  - Order staging validation.
- Ensure existing short put collateral uses actual strike, not average cost or stock position data.
- Acceptance criteria:
  - Tests cover multiple existing short puts, multiple contracts, missing strike, and cash reserve disabled.

### TODO 2.4: Add exact contract refresh before order staging

- Before staging or executing, refresh:
  - bid
  - ask
  - mid
  - stock price
  - spread
  - quote timestamp
  - buying power/cash availability
- Acceptance criteria:
  - User sees "verified just now" or "stale quote" before staging.
  - If price moved beyond tolerance, user must re-confirm.

## Phase 3: Covered Call Polish

### TODO 3.1: Make covered call intent explicit

- Classify each covered call signal:
  - Income-only: strike safely above price and cost basis.
  - Profit-taking: strike implies acceptable called-away return.
  - Repair/risk-reduction: below cost basis or tight upside.
- Acceptance criteria:
  - UI labels the intent on each covered call.
  - Score details weight cost basis and if-called return visibly.

### TODO 3.2: Improve covered call sizing defaults

- Stop defaulting recommendations to all available covered lots without showing sizing rationale.
- Add recommended quantity based on:
  - available uncovered shares
  - risk mode
  - existing short calls
  - concentration in ticker
- Keep user override.
- Acceptance criteria:
  - Recommendation card says "Recommended qty" and "Max covered qty".
  - Execute/stage uses recommended quantity, not max, unless user changes it.

### TODO 3.3: Add assignment consequence display

- For every covered call candidate, show:
  - if-called proceeds
  - if-called return
  - distance to cost basis
  - shares remaining after assignment
  - dividend/earnings warning if available
- Acceptance criteria:
  - User can evaluate assignment outcome without mental math.

## Phase 4: Cash-Secured Put Polish

### TODO 4.1: Make CSP collateral and concentration unavoidable

- For every put candidate, show:
  - cash required
  - percent of available cash
  - percent of account value
  - cash remaining after staging
  - existing short put collateral
  - ticker concentration if assigned
- Acceptance criteria:
  - A CSP cannot be staged without visible collateral impact.

### TODO 4.2: Improve CSP buffer model

- Show both:
  - OTM distance.
  - Breakeven buffer.
  - Expected move buffer.
- Penalize candidates where expected move exceeds OTM distance unless premium/IV context justifies it.
- Acceptance criteria:
  - Score details include expected move buffer.
  - Tests cover high IV wide expected move cases.

### TODO 4.3: Add assignment readiness wording

- Label puts as:
  - "Would own at attractive basis"
  - "Premium harvest only"
  - "Thin buffer"
  - "Assignment not attractive"
- Use existing fields; do not add a new strategy.
- Acceptance criteria:
  - Rationale explains whether the signal is acceptable if assigned.

## Phase 5: Recommendation Ranking And Explanation

### TODO 5.1: Show why this ranked above alternatives

- For each top recommendation, include the top 3 positive drivers and top 3 risk drags.
- Example:
  - Drivers: high IV rank, tight spread, good buffer.
  - Drags: earnings in 6 days, lower OI, macro penalty.
- Acceptance criteria:
  - No card only says "Score: 84.2" without explanation.

### TODO 5.2: Add "near miss" diagnostics

- For each ticker, record why no signal appeared:
  - no quote
  - no expirations in profile
  - spread too wide
  - too little premium
  - not enough cash
  - not enough shares
  - near earnings
- Acceptance criteria:
  - Options table can show a useful empty state per ticker.
  - Diagnostics are available in API response under debug/dev flag or diagnostics payload.

### TODO 5.3: Revisit ticker diversity rule

- Current top recommendations allow max one per ticker.
- Keep this if the goal is ticker diversification, but explain it.
- Consider separate best CALL and best PUT lists, each with ticker diversity, instead of one mixed list.
- Acceptance criteria:
  - User can tell whether a strong candidate was hidden due to diversity.

### TODO 5.4: Add stable score bands

- Define score bands based on tested behavior:
  - 90+: exceptional setup.
  - 80-89: strong setup.
  - 65-79: acceptable with review.
  - below 65: weak or special case.
- Bands must be backed by fixtures, not vibes.
- Acceptance criteria:
  - README/SCORING docs match code.
  - UI badges match documented bands.

## Phase 6: UI/UX Polish For The Existing Surfaces

### TODO 6.1: Make staging and execution visually distinct

- Use "Stage" for saving an order.
- Use "Send order" or "Execute" only for broker submission.
- Put execution buttons behind stronger color/confirmation.
- Acceptance criteria:
  - No "Sell", "Add", "Stage", and "Execute" ambiguity in the same surface.

### TODO 6.2: Add execution pre-flight modal

- Before execution, show:
  - ticker, type, strike, expiration, DTE
  - quantity
  - limit price
  - bid/ask/mid and spread
  - source and quote freshness
  - cash/share impact
  - all hard warnings
  - account mode: SIMULATE or REAL
- Acceptance criteria:
  - "Execute now" never bypasses this modal.

### TODO 6.3: Fix percent and numeric display

- IV: show decimal IV as percent.
- Delta: show signed delta and optionally `abs(delta)` as probability proxy.
- Premium: always show per contract and total.
- Cash required: include commas and currency formatting.
- Annualized return: label denominator.
- Acceptance criteria:
  - One formatter module handles all display units.

### TODO 6.4: Reduce table overload without removing data

- Primary table columns:
  - ticker
  - type
  - strike / expiry / DTE
  - score / confidence
  - premium
  - return
  - delta / buffer
  - spread / liquidity
  - warnings
  - action
- Move secondary details into expandable row.
- Acceptance criteria:
  - User can scan top candidates without horizontal hunting.

### TODO 6.5: Adopt `StateModel` everywhere

- Use the same state model for:
  - account summary
  - top recommendations
  - options table
  - pending orders
  - rollover table
  - macro/VIX cards
- Acceptance criteria:
  - Loading, empty, stale, error, and OpenD unavailable states have consistent copy and retry behavior.

### TODO 6.6: Remove production console noise

- Remove routine `console.log` from dashboard modules.
- Keep meaningful `console.error` for unexpected failures.
- Add a lint/check for production console logs if possible.
- Acceptance criteria:
  - Browser console is quiet during normal dashboard load and refresh.

## Phase 7: Performance And Refresh Flow

### TODO 7.1: Progressive signal loading

- Load in this order:
  - connection/account state
  - pending orders and open positions
  - top recommendations from cache
  - fresh recommendation refresh
  - full options table
- Acceptance criteria:
  - First useful state appears even if option chain fetch is slow.

### TODO 7.2: Refresh only what changed

- After staging an order, refresh pending orders and affected ticker cash/share availability.
- After execution, refresh account, positions, pending orders, and recommendations.
- After changing OTM/expiration, refresh only that ticker/side.
- Acceptance criteria:
  - No full dashboard reload for a single ticker refresh.

### TODO 7.3: Add quote staleness policy

- Define maximum age for:
  - display-only candidate
  - stage order
  - execute order
- Acceptance criteria:
  - UI marks stale signals.
  - Execute path refreshes stale quotes.

## Phase 8: Documentation And Validation

### TODO 8.1: Rewrite scoring docs from code

- Update `SCORING.md` to document:
  - hard filters
  - scoring factors
  - score bands
  - confidence score
  - data sources
  - limitations
- Acceptance criteria:
  - Every displayed score detail has a matching docs entry.

### TODO 8.2: Add route/schema contract tests

- Test recommendation response shape.
- Test options table response shape.
- Test error envelopes.
- Test unit conventions for IV, IV rank, delta, return, and cash.
- Acceptance criteria:
  - Frontend no longer has to sanitize `NaN` from backend responses.

### TODO 8.3: Add browser smoke tests for current workflow

- Dashboard loads with no console errors.
- OpenD unavailable state renders safely.
- Recommendations render from mocked API.
- Stage order from recommendation.
- Stage order from options table.
- Execute path opens pre-flight modal.
- Acceptance criteria:
  - Smoke tests run without live broker connection.

## Recommended Implementation Order

1. Phase 0: validation and dependency foundation.
2. Phase 1: scoring correctness and units.
3. Phase 2: source metadata and broker confirmation before execution.
4. Phase 6.1-6.3: action clarity and formatting.
5. Phase 3 and Phase 4: covered call and CSP domain polish.
6. Phase 5: explanation and diagnostics.
7. Phase 7: performance and refresh flow.
8. Phase 8: docs and long-term automation.

## Definition Of Done

- The generator can explain every top signal in plain language.
- Every candidate has clear score, confidence, source, freshness, and warnings.
- Covered calls show assignment consequence before action.
- Cash-secured puts show collateral and assignment readiness before action.
- Broker execution requires refreshed broker quote and explicit confirmation.
- Tests run without OpenD side effects.
- No user-facing mojibake or misleading percent formatting.
- The dashboard feels calmer because weak, stale, or low-confidence signals are quieter than strong verified signals.
