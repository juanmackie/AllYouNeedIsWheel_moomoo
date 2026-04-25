"""
LLM Advisor Service

Stateless module that aggregates trading data and calls an LLM for
actionable wheel-strategy suggestions (opens, closes, rolls).

Uses the OpenAI Python client, which works with OpenAI, Anthropic
(compatible mode), OpenRouter, Ollama, and any OpenAI-compatible API.

Configuration via environment variables:
    LLM_ENABLED   — 'true' or 'false' (default: false)
    LLM_PROVIDER  — label for logging only
    LLM_API_KEY   — API key
    LLM_MODEL     — model name (default: gpt-4o)
    LLM_BASE_URL  — optional base URL override
    LLM_TEMPERATURE — optional (default: 0.3)
    LLM_MAX_TOKENS  — optional (default: 2000)
"""

import os
import logging
import traceback

logger = logging.getLogger('autotrader.llm')


def _is_enabled():
    return os.environ.get('LLM_ENABLED', 'false').strip().lower() in {'1', 'true', 'yes', 'y', 'on'}


def _get_client():
    """Create OpenAI client from environment config. Returns None if not configured."""
    if not _is_enabled():
        return None

    api_key = os.environ.get('LLM_API_KEY', '').strip()
    if not api_key or api_key.startswith('sk-your-'):
        logger.warning("LLM_API_KEY not set or is placeholder — LLM advisor disabled")
        return None

    from openai import OpenAI

    base_url = os.environ.get('LLM_BASE_URL', '').strip() or None
    return OpenAI(api_key=api_key, base_url=base_url)


def build_advisor_context():
    """
    Gather portfolio, positions, opportunities, and macro context
    into a structured dictionary for prompt construction.
    """
    context = {
        'portfolio': {},
        'positions': [],
        'opportunities': [],
        'macro': {},
        'vix': {},
        'scored_positions': [],
    }

    # --- Portfolio summary ---
    try:
        from api import get_service
        portfolio_svc = get_service('portfolio')
        summary = portfolio_svc.get_portfolio_summary() or {}
        context['portfolio'] = {
            'account_value': summary.get('account_value', 0),
            'available_cash': summary.get('available_cash', 0),
            'currency': summary.get('currency', 'USD'),
        }
    except Exception as exc:
        logger.warning(f"Could not load portfolio summary: {exc}")

    # --- Stock positions ---
    try:
        from api import get_service
        portfolio_svc = get_service('portfolio')
        stocks = portfolio_svc.get_positions('STK') or []
        context['positions'] = [
            {
                'ticker': _bare_ticker(p.get('symbol', '')),
                'shares': int(p.get('position', 0) or 0),
                'avg_cost': float(p.get('avg_cost', 0) or 0),
                'market_price': float(p.get('market_price', 0) or 0),
            }
            for p in stocks
            if int(p.get('position', 0) or 0) > 0
        ]
    except Exception as exc:
        logger.warning(f"Could not load stock positions: {exc}")

    # --- Option positions with roll-pressure scoring ---
    try:
        from api import get_service
        portfolio_svc = get_service('portfolio')
        from core.wheel_decision import score_existing_position
        from api.services.iv_earnings_service import IVEarningsService
        from api.services.macro_regime_service import get_macro_service

        opt_positions = portfolio_svc.get_positions('OPT') or []
        positions_map = {
            _bare_ticker(p.get('symbol', '')): p
            for p in (portfolio_svc.get_positions('STK') or [])
        }

        # Minimal portfolio context for scorer
        macro = get_macro_service().get_macro_regime()
        pf_context = {
            'positions': positions_map,
            'cash_balance': float((summary or {}).get('available_cash', 0)),
            'account_value': float((summary or {}).get('account_value', 0)),
            'short_calls': {},
            'short_puts': {},
            'vix_regime': {'regime': 'normal', 'vix': 20.0},
        }

        db_path = None
        try:
            from api.services.config import get_config
            db_path = get_config().get('db_path')
        except Exception:
            pass

        scored = []
        from datetime import datetime
        for pos in opt_positions:
            ticker = _bare_ticker(pos.get('symbol', ''))
            qty = int(pos.get('position', 0) or 0)
            if qty >= 0:
                continue  # only short options matter

            try:
                from api import get_service
                options_svc = get_service('options')
                price = options_svc.get_stock_price(ticker)
                if not price or price <= 0:
                    continue
            except Exception:
                continue

            exp_str = str(pos.get('expiration', '') or '')
            try:
                exp_date = datetime.strptime(exp_str, '%Y%m%d').date()
                dte = (exp_date - datetime.now().date()).days
            except (ValueError, TypeError):
                dte = 0

            pos_data = {
                'option_type': str(pos.get('option_type', '') or '').upper(),
                'strike': float(pos.get('strike', 0) or 0),
                'expiration': exp_str,
                'dte': dte,
                'bid': float(pos.get('bid', 0) or 0),
                'ask': float(pos.get('ask', 0) or 0),
                'last': float(pos.get('market_price', 0) or 0),
                'delta': float(pos.get('delta', 0) or 0),
                'theta': float(pos.get('theta', 0) or 0),
                'implied_volatility': float(pos.get('implied_volatility', 0) or 0),
            }

            try:
                from db.database import OptionsDatabase
                iv_svc = IVEarningsService(OptionsDatabase(db_path))
                iv = pos_data['implied_volatility']
                iv_adj, iv_rank, iv_status = iv_svc.get_iv_environment_score(
                    ticker, iv if iv > 0 else 0.20
                )
                earn_adj, _ = iv_svc.get_earnings_score_impact(ticker)
            except Exception:
                iv_adj, iv_rank, iv_status = 1.0, 50, 'neutral'
                earn_adj = 1.0

            decision = score_existing_position(
                ticker=ticker,
                position_data=pos_data,
                current_stock_price=price,
                portfolio_context=pf_context,
                iv_env_adjustment=iv_adj,
                iv_rank=iv_rank,
                iv_status_str=iv_status,
                earnings_adjustment=earn_adj,
                macro_regime=macro,
            )

            scored.append({
                'ticker': decision.ticker,
                'option_type': decision.option_type,
                'strike': decision.strike,
                'expiration': decision.expiration,
                'dte': decision.dte,
                'stock_price': decision.stock_price,
                'mid_price': decision.mid_price,
                'otm_pct': decision.otm_pct,
                'roll_pressure': decision.roll_pressure,
                'profit_target_progress': decision.profit_target_progress,
                'extrinsic_remaining': decision.extrinsic_remaining,
                'warnings': decision.warnings,
            })
        context['scored_positions'] = scored
    except Exception as exc:
        logger.warning(f"Could not score option positions: {exc}")

    # --- Top recommendations ---
    try:
        from api import get_service
        options_svc = get_service('options')
        recs = options_svc.get_top_recommendations(limit=5)
        for r in recs.get('recommendations', [])[:5]:
            context['opportunities'].append({
                'ticker': r.get('ticker', ''),
                'option_type': r.get('option_type', ''),
                'strike': r.get('strike', 0),
                'expiration': r.get('expiration', ''),
                'premium_per_contract': r.get('premium_per_contract', 0),
                'delta': r.get('delta', 0),
                'dte': r.get('dte', 0),
                'annualized_return': r.get('annualized_return', 0),
                'score': r.get('score', 0),
                'warnings': r.get('warnings', []),
            })
    except Exception as exc:
        logger.warning(f"Could not load recommendations: {exc}")

    # --- Macro / VIX ---
    try:
        from api.services.macro_regime_service import get_macro_service
        context['macro'] = get_macro_service().get_macro_regime()
    except Exception:
        pass

    try:
        from api import get_service
        options_svc = get_service('options')
        context['vix'] = options_svc._get_vix_regime()
    except Exception:
        pass

    return context


def format_prompt(context):
    """Convert the context dict into a readable prompt for the LLM."""
    lines = []
    pf = context['portfolio']
    pos = context['positions']
    opt = context['scored_positions']
    opp = context['opportunities']
    macro = context['macro']
    vix = context['vix']

    # Portfolio snapshot
    lines.append("=== PORTFOLIO ===")
    lines.append(f"Account value: ${pf.get('account_value', 0):,.2f}")
    lines.append(f"Available cash: ${pf.get('available_cash', 0):,.2f}")

    if pos:
        lines.append("\nStock positions:")
        for s in pos:
            lines.append(
                f"  {s['ticker']}: {s['shares']} shares "
                f"@ ${s['avg_cost']:.2f} avg, "
                f"current ${s['market_price']:.2f}"
            )

    # Open short options (scored)
    if opt:
        lines.append("\nOpen short options:")
        for o in sorted(opt, key=lambda x: x['roll_pressure'], reverse=True):
            lines.append(
                f"  {o['ticker']} {o['option_type']} ${o['strike']:.1f} "
                f"exp {o['expiration']} DTE={o['dte']} | "
                f"stock=${o['stock_price']:.2f} OTM={o['otm_pct']:.1f}% | "
                f"mid=${o['mid_price']:.2f} roll_pressure={o['roll_pressure']:.0f}% "
                f"profit_progress={o['profit_target_progress']:.0f}%"
            )
            if o.get('warnings'):
                for w in o['warnings']:
                    lines.append(f"    ⚠ {w}")
    else:
        lines.append("\nNo open short options.")

    # Opportunities
    if opp:
        lines.append("\n=== TOP OPPORTUNITIES ===")
        for i, r in enumerate(opp):
            lines.append(
                f"  #{i+1} {r['ticker']} {r['option_type']} ${r['strike']:.1f} "
                f"exp {r['expiration']} | "
                f"premium=${r['premium_per_contract']:.2f} "
                f"delta={r['delta']:.3f} "
                f"DTE={r['dte']} "
                f"ann_return={r['annualized_return']:.1f}% "
                f"score={r['score']:.0f}"
            )
    else:
        lines.append("\nNo top opportunities available.")

    # Market context
    lines.append("\n=== MARKET CONTEXT ===")
    if vix:
        lines.append(
            f"VIX: {vix.get('vix', 'N/A')} "
            f"({vix.get('regime', 'unknown')}) — "
            f"{vix.get('description', '')}"
        )
    if macro:
        lines.append(
            f"Macro: rates {macro.get('rate_regime', 'unknown')}, "
            f"credit stress {macro.get('credit_stress', 'unknown')}, "
            f"growth {macro.get('growth_regime', 'unknown')}, "
            f"inflation {macro.get('inflation_trend', 'unknown')}"
        )
        lines.append(f"Yield curve: {macro.get('yield_curve_slope', 'N/A')}")
        lines.append(f"Summary: {macro.get('summary', 'N/A')}")
        lines.append(f"Wheel advice: {macro.get('advice', 'N/A')}")

    lines.append("\n---")
    lines.append(
        "Based on the data above, provide specific trade suggestions for: "
        "OPENING new covered calls or CSPs, CLOSING positions that have reached "
        "profit targets, and ROLLING positions under pressure. "
        "For each suggestion, mention the ticker, strike, expiration, and rationale. "
        "Keep the response concise and actionable."
    )

    return '\n'.join(lines)


def get_suggestions():
    """
    Main entry point. Gathers context, calls the LLM, returns the advice text.

    Returns:
        dict with:
            'success': True/False
            'text': str — the LLM's response (only on success)
            'error': str — error message (only on failure)
            'provider': str — the LLM provider label
            'model': str — the model name
    """
    if not _is_enabled():
        return {
            'success': False,
            'error': 'LLM advisor is disabled. Set LLM_ENABLED=true and LLM_API_KEY in .env.',
        }

    client = _get_client()
    if client is None:
        return {
            'success': False,
            'error': 'LLM not configured. Set LLM_API_KEY in .env (not the placeholder).',
        }

    model = os.environ.get('LLM_MODEL', 'gpt-4o').strip()
    provider = os.environ.get('LLM_PROVIDER', 'openai').strip()

    try:
        context = build_advisor_context()
        prompt = format_prompt(context)
    except Exception as exc:
        logger.error(f"Failed to build advisor context: {exc}")
        logger.error(traceback.format_exc())
        return {
            'success': False,
            'error': f'Failed to gather trading data: {exc}',
        }

    system_prompt = (
        "You are an expert options wheel strategy advisor. "
        "Analyze the trading data below and give specific, actionable suggestions "
        "for trade opens, closes, and rolls. Use a balanced risk stance: follow "
        "standard wheel mechanics, close at 50% profit, roll at 21 DTE or when "
        "strike is threatened. Be concise. Mention ticker, strike, expiration, "
        "and brief rationale for each suggestion."
    )

    try:
        temperature = float(os.environ.get('LLM_TEMPERATURE', '0.3'))
        max_tokens = int(os.environ.get('LLM_MAX_TOKENS', '2000'))

        logger.info(f"Calling LLM ({provider}/{model}) for trade suggestions")
        response = client.chat.completions.create(
            model=model,
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': prompt},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        message = response.choices[0].message
        content = message.content
        if content is None:
            # Try to get reasoning (for models like tencent/hy3-preview)
            reasoning = getattr(message, 'reasoning', None)
            if reasoning:
                content = reasoning
            else:
                content = ''
        text = content.strip()
        logger.info(f"LLM response received ({len(text)} chars)")

        return {
            'success': True,
            'text': text,
            'provider': provider,
            'model': model,
        }
    except Exception as exc:
        logger.error(f"LLM call failed: {exc}")
        logger.error(traceback.format_exc())
        return {
            'success': False,
            'error': f'LLM call failed: {exc}',
        }


def _bare_ticker(symbol):
    """Strip market prefix from a symbol (e.g., 'US.AAPL' -> 'AAPL')."""
    if not symbol:
        return ''
    s = str(symbol)
    if '.' in s:
        return s.split('.', 1)[1]
    return s
