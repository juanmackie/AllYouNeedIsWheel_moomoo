"""
Risk Sizing Routes
Exposes ATR-based position sizing via REST API.
"""

import logging
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from flask import Blueprint, request, jsonify
from api.routes.utils import error_response, success_response
from api.services.risk_sizing_service import get_risk_sizing_service

logger = logging.getLogger(__name__)

bp = Blueprint('risk', __name__, url_prefix='/api/risk')


class SizingQuery(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ticker: str = Field(min_length=1)
    account_value: float = Field(default=45000, gt=0)
    risk_pct: float = Field(default=0.01, gt=0, le=1)
    atr_period: int = Field(default=14, gt=0)

    @field_validator('ticker', mode='before')
    @classmethod
    def _normalize_ticker(cls, value):
        return str(value).strip().upper()


class BatchSizingRequest(BaseModel):
    tickers: list[str] = Field(default_factory=list)
    account_value: float = Field(default=45000, gt=0)
    risk_pct: float = Field(default=0.01, gt=0, le=1)

    @field_validator('tickers', mode='before')
    @classmethod
    def _normalize_tickers(cls, value):
        if value is None:
            return []
        return [str(t).strip().upper() for t in value if str(t).strip()]


def _validation_error(exc: ValidationError):
    message = '; '.join(err.get('msg', 'Invalid value') for err in exc.errors()) or 'Invalid request parameters'
    return error_response(message, status_code=400)


@bp.route('/sizing')
def get_sizing():
    """
    Get ATR-based position sizing for a ticker.

    Query Parameters:
        ticker: Stock symbol (e.g., AAPL)
        account_value: Total account value (default: 45000)
        risk_pct: Risk percentage (default: 0.01 = 1%)
        atr_period: ATR period (default: 14)

    Returns:
        JSON with sizing breakdown.
    """
    try:
        params = SizingQuery.model_validate(request.args.to_dict())

        service = get_risk_sizing_service()
        result = service.calculate_position_size(
            ticker=params.ticker,
            account_value=params.account_value,
            risk_pct=params.risk_pct,
            atr_period=params.atr_period,
        )

        return success_response({
            'data': result,
        })

    except ValidationError as exc:
        return _validation_error(exc)
    except Exception as e:
        logger.error(f"Error calculating position size: {e}")
        return error_response(str(e))


@bp.route('/sizing/batch', methods=['POST'])
def get_batch_sizing():
    """
    Get ATR-based position sizing for multiple tickers.

    POST Body:
        { "tickers": ["AAPL", "MSFT"], "account_value": 45000, "risk_pct": 0.01 }

    Returns:
        JSON with sizing for each ticker.
    """
    try:
        payload = BatchSizingRequest.model_validate(request.get_json(silent=True) or {})
        if not payload.tickers:
            return error_response('No tickers provided', status_code=400)

        service = get_risk_sizing_service()
        results = {}

        for ticker in payload.tickers:
            try:
                result = service.calculate_position_size(
                    ticker=ticker,
                    account_value=payload.account_value,
                    risk_pct=payload.risk_pct,
                )
                results[ticker] = result
            except Exception as e:
                logger.error(f"Error calculating size for {ticker}: {e}")
                results[ticker] = {'error': str(e)}

        return success_response({
            'data': results,
        })

    except ValidationError as exc:
        return _validation_error(exc)
    except Exception as e:
        logger.error(f"Error in batch sizing: {e}")
        return error_response(str(e))


@bp.route('/sizing/cache/clear', methods=['POST'])
def clear_sizing_cache():
    """Clear the risk sizing cache."""
    try:
        service = get_risk_sizing_service()
        service.clear_cache()
        return success_response({
            'message': 'Risk sizing cache cleared'
        })
    except Exception as e:
        logger.error(f"Error clearing sizing cache: {e}")
        return error_response(str(e))
