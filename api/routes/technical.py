"""
Technical Regime API Routes
Exposes technical market regime detection (200 EMA + ADX) via REST API.
"""

import logging
from flask import Blueprint, request, jsonify
from api.routes.utils import error_response, success_response
from api.services.technical_regime_service import get_technical_regime_service
from api.services.utils import validate_ticker

logger = logging.getLogger(__name__)

bp = Blueprint('technical', __name__, url_prefix='/api/technical')


@bp.route('/regime')
def get_regime():
    """
    Get technical market regime for one or more tickers.

    Query Parameters:
        tickers: Comma-separated ticker symbols (e.g., 'AAPL,MSFT,TSLA')

    Returns:
        JSON with regime data for each ticker.
    """
    tickers_param = request.args.get('tickers', '').strip()
    ticker = request.args.get('ticker', '').strip()

    # Support both 'tickers' (plural) and 'ticker' (single)
    if tickers_param:
        tickers = [t.strip().upper() for t in tickers_param.split(',') if t.strip()]
    elif ticker:
        tickers = [ticker.strip().upper()]
    else:
        return error_response('Missing required parameter: tickers or ticker', status_code=400)
    
    # ── Entry gate: filter out invalid tickers ──
    valid_tickers = [t for t in tickers if validate_ticker(t)]
    skipped = [t for t in tickers if t not in valid_tickers]
    if skipped:
        logger.warning(f"Technical regime: Skipping {len(skipped)} invalid ticker(s): {skipped}")
    if not valid_tickers:
        return error_response('No valid tickers provided', status_code=400)

    try:
        service = get_technical_regime_service()
        results = service.get_batch_regimes(valid_tickers)

        return success_response({
            'data': results,
            'timestamp': results.get(valid_tickers[0], {}).get('updated_at') if len(valid_tickers) == 1 else None,
        })
    except Exception as e:
        logger.error(f"Error getting technical regime: {e}")
        return error_response(str(e))


@bp.route('/regime/<ticker>')
def get_regime_single(ticker):
    """
    Get technical market regime for a single ticker.

    URL Parameters:
        ticker: Ticker symbol (e.g., AAPL)

    Returns:
        JSON with regime data.
    """
    ticker = ticker.strip().upper()
    
    # ── Entry gate: validate ticker ──
    if not validate_ticker(ticker):
        return error_response(f'Invalid ticker: {ticker}', status_code=400)

    try:
        service = get_technical_regime_service()
        regime = service.get_combined_regime(ticker)

        return success_response({
            'data': regime,
        })
    except Exception as e:
        logger.error(f"Error getting technical regime for {ticker}: {e}")
        return error_response(str(e))


@bp.route('/regime/summary')
def get_regime_summary():
    """
    Get a summary of technical regimes for the entire watchlist.

    Query Parameters:
        tickers: Comma-separated ticker symbols

    Returns:
        JSON with summary: bullish/bearish/neutral counts, dominant regime.
    """
    tickers_param = request.args.get('tickers', '').strip()
    if not tickers_param:
        return error_response('Missing required parameter: tickers', status_code=400)

    tickers = [t.strip().upper() for t in tickers_param.split(',') if t.strip()]
    
    # ── Entry gate: filter out invalid tickers ──
    valid_tickers = [t for t in tickers if validate_ticker(t)]
    skipped = [t for t in tickers if t not in valid_tickers]
    if skipped:
        logger.warning(f"Technical regime summary: Skipping {len(skipped)} invalid ticker(s): {skipped}")
    if not valid_tickers:
        return error_response('No valid tickers provided', status_code=400)

    try:
        service = get_technical_regime_service()
        results = service.get_batch_regimes(valid_tickers) 
        
        # Use valid_tickers for downstream logic
        tickers = valid_tickers

        # Summarize
        regimes = {'bullish': 0, 'bearish': 0, 'neutral': 0}
        trending = 0
        ranging = 0

        for ticker, data in results.items():
            regime = data.get('regime', 'neutral')
            if regime in regimes:
                regimes[regime] += 1
            if data.get('trend_strength') == 'trending':
                trending += 1
            else:
                ranging += 1

        # Determine dominant regime
        dominant = max(regimes, key=regimes.get) if any(regimes.values()) else 'neutral'

        summary = {
            'total': len(tickers),
            'regimes': regimes,
            'trending': trending,
            'ranging': ranging,
            'dominant_regime': dominant,
            'summary_text': _generate_summary_text(regimes, dominant),
        }

        return success_response({
            'summary': summary,
            'details': results,
        })
    except Exception as e:
        logger.error(f"Error getting regime summary: {e}")
        return error_response(str(e))


def _generate_summary_text(regimes, dominant):
    """Generate human-readable summary text."""
    total = sum(regimes.values())
    if total == 0:
        return "No data available"

    bullish_pct = (regimes['bullish'] / total) * 100
    bearish_pct = (regimes['bearish'] / total) * 100

    if dominant == 'bullish':
        emoji = '🟢'
        text = f"Bullish bias — {regimes['bullish']}/{total} tickers ({bullish_pct:.0f}%) are bullish"
    elif dominant == 'bearish':
        emoji = '🔴'
        text = f"Bearish bias — {regimes['bearish']}/{total} tickers ({bearish_pct:.0f}%) are bearish"
    else:
        emoji = '⚪'
        text = f"Neutral market — {regimes['neutral']}/{total} tickers are neutral"

    return f"{emoji} {text}"


@bp.route('/regime/cache/clear', methods=['POST'])
def clear_regime_cache():
    """Clear the technical regime cache."""
    try:
        service = get_technical_regime_service()
        service.clear_cache()
        return success_response({
            'message': 'Technical regime cache cleared'
        })
    except Exception as e:
        logger.error(f"Error clearing regime cache: {e}")
        return error_response(str(e))
