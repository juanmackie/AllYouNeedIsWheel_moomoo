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
    LLM_MODEL     — model name (default: moonshotai/kimi-k2.6)
    LLM_BASE_URL  — optional base URL override
    LLM_TEMPERATURE — optional (default: 0.3)
    LLM_MAX_TOKENS  — optional (default: 2000)
"""

import os
import logging
import traceback
from datetime import datetime

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
    timeout_seconds = float(os.environ.get('LLM_TIMEOUT_SECONDS', '20'))
    max_retries = int(os.environ.get('LLM_MAX_RETRIES', '0'))
    return OpenAI(api_key=api_key, base_url=base_url, timeout=timeout_seconds, max_retries=max_retries)


def get_status():
    """Return lightweight LLM advisor availability without creating a client."""
    enabled = _is_enabled()
    api_key = os.environ.get('LLM_API_KEY', '').strip()
    configured = bool(api_key) and not api_key.startswith('sk-your-')
    provider = os.environ.get('LLM_PROVIDER', 'openai').strip()
    model = os.environ.get('LLM_MODEL', 'moonshotai/kimi-k2.6').strip()

    if not enabled:
        message = 'LLM advisor is disabled.'
    elif not configured:
        message = 'LLM advisor needs LLM_API_KEY before suggestions can run.'
    else:
        message = 'LLM advisor is ready.'

    return {
        'enabled': enabled,
        'configured': configured,
        'available': enabled and configured,
        'provider': provider,
        'model': model,
        'message': message,
    }


def build_advisor_context():
    """
    Gather portfolio, positions, opportunities, and macro context
    into a structured dictionary for evidence-gated prompt construction.
    """
    from core.evidence_gated_advisor import build_evidence_from_context

    context = {
        'portfolio': {},
        'positions': [],
        'opportunities': [],
        'signal_overlays': [],
        'macro': {},
        'vix': {},
        'scored_positions': [],
        'current_datetime': datetime.now().strftime('%Y-%m-%d %H:%M:%S %Z'),
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
            logger.warning("Could not load config db_path for LLM context", exc_info=True)

        scored = []
        for pos in opt_positions:
            ticker = _bare_ticker(pos.get('symbol', ''))
            qty = int(pos.get('position', 0) or 0)
            if qty >= 0:
                continue

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

    # --- Top signals ---
    try:
        from api import get_service
        options_svc = get_service('options')
        recs = options_svc.get_top_recommendations(limit=5)
        for r in recs.get('signals', [])[:5]:
            overlay = r.get('signal_overlay') or {}
            if overlay:
                context['signal_overlays'].append({
                    'ticker': r.get('ticker', ''),
                    'signal_type': r.get('signal_type', ''),
                    'option_type': r.get('option_type', ''),
                    'overlay': overlay,
                    'fit': r.get('signal_overlay_fit', 'unknown'),
                    'warnings': r.get('signal_overlay_warnings', []),
                })
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
                'signal_overlay': overlay,
                'signal_overlay_fit': r.get('signal_overlay_fit', 'unknown'),
            })
    except Exception as exc:
        logger.warning(f"Could not load signals: {exc}")

    # --- Macro / VIX ---
    try:
        from api.services.macro_regime_service import get_macro_service
        context['macro'] = get_macro_service().get_macro_regime()
    except Exception:
        logger.warning("Could not load macro regime for LLM context", exc_info=True)

    try:
        from api import get_service
        options_svc = get_service('options')
        context['vix'] = options_svc._get_vix_regime()
    except Exception:
        logger.warning("Could not load VIX regime for LLM context", exc_info=True)

    return context


def format_prompt(context):
    """
    Build an evidence-gated prompt for the LLM.
    The LLM may ONLY reference evidence surfaced here.
    If evidence is missing, answer 'unknown'.
    """
    from core.evidence_gated_advisor import build_evidence_from_context
    evidence = build_evidence_from_context(context)
    return evidence.to_prompt()


def _coerce_message_text(value):
    """Extract displayable text from OpenAI/OpenRouter message fields."""
    if value is None:
        return ''
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        parts = [_coerce_message_text(item).strip() for item in value]
        return '\n'.join(part for part in parts if part)
    if isinstance(value, dict):
        for key in ('text', 'content', 'reasoning', 'reasoning_content', 'output_text', 'refusal'):
            text = _coerce_message_text(value.get(key)).strip()
            if text:
                return text
        return ''

    for attr in ('text', 'content', 'reasoning', 'reasoning_content', 'output_text', 'refusal'):
        text = _coerce_message_text(getattr(value, attr, None)).strip()
        if text:
            return text
    return ''


def _message_to_mapping(message):
    if isinstance(message, dict):
        return message
    if hasattr(message, 'model_dump'):
        try:
            return message.model_dump()
        except Exception:
            return None
    if hasattr(message, 'dict'):
        try:
            return message.dict()
        except Exception:
            return None
    return None


def _extract_message_text(message):
    """Return the best displayable assistant text from a provider response message."""
    for attr in ('content', 'reasoning', 'reasoning_content', 'output_text', 'text', 'refusal'):
        text = _coerce_message_text(getattr(message, attr, None)).strip()
        if text:
            return text

    mapping = _message_to_mapping(message)
    if not mapping:
        return ''

    for key in ('content', 'reasoning', 'reasoning_content', 'output_text', 'text', 'refusal'):
        text = _coerce_message_text(mapping.get(key)).strip()
        if text:
            return text

    for key in ('additional_kwargs', 'metadata', 'response_metadata'):
        nested = mapping.get(key)
        text = _coerce_message_text(nested).strip()
        if text:
            return text

    return ''


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

    model = os.environ.get('LLM_MODEL', 'moonshotai/kimi-k2.6').strip()
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
        "You MUST follow these rules strictly:\n"
        "1. You may ONLY reference evidence provided below in the [EVIDENCE] section.\n"
        "2. If evidence for a category says 'unknown', answer 'unknown' — do NOT infer.\n"
        "3. For each suggestion, cite the specific evidence that supports it.\n"
        "4. Use a balanced risk stance: follow standard wheel mechanics, "
        "close at 50% profit, roll at 21 DTE or when strike is threatened.\n"
        "5. Be concise. Mention ticker, strike, expiration, and brief rationale.\n"
        "6. Never mention data, news, or analysis that is not in the evidence block."
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
        text = _extract_message_text(message).strip()
        logger.info(f"LLM response received ({len(text)} chars)")

        if not text:
            logger.warning("LLM provider returned an empty advisor response")
            return {
                'success': False,
                'error': 'LLM provider returned an empty response. Try again or choose a different model.',
                'provider': provider,
                'model': model,
            }

        return {
            'success': True,
            'text': text,
            'provider': provider,
            'model': model,
        }
    except Exception as exc:
        if '429' in str(exc) or exc.__class__.__name__ == 'RateLimitError':
            logger.error("LLM provider rate-limited upstream: %s", exc)
            return {
                'success': False,
                'error': 'LLM provider is rate-limited upstream. Retry later or use your own API key/model.',
                'provider': provider,
                'model': model,
            }
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
