"""
Probability of Profit API Routes
"""

import logging
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from flask import Blueprint, request, jsonify
from api.routes.utils import error_response, success_response
from api.services.pop_service import get_pop, calculate_pop_delta

logger = logging.getLogger(__name__)

bp = Blueprint('pop', __name__, url_prefix='/api/pop')


class PopEstimateQuery(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ticker: str = Field(min_length=1)
    strike: float = Field(gt=0)
    expiration: str = Field(min_length=1)
    option_type: str = Field(alias='type')
    delta: float | None = None
    iv: float | None = None
    dte: int | None = Field(default=None, gt=0)
    method: str = Field(default='delta')

    @field_validator('ticker', mode='before')
    @classmethod
    def _normalize_ticker(cls, value):
        return str(value).strip().upper()

    @field_validator('expiration', mode='before')
    @classmethod
    def _normalize_expiration(cls, value):
        return str(value).strip()

    @field_validator('option_type', mode='before')
    @classmethod
    def _normalize_option_type(cls, value):
        return str(value).strip().upper()

    @field_validator('method', mode='before')
    @classmethod
    def _normalize_method(cls, value):
        return str(value).strip().lower()


def _validation_error(exc: ValidationError):
    message = '; '.join(err.get('msg', 'Invalid value') for err in exc.errors()) or 'Invalid request parameters'
    return error_response(message, status_code=400)


@bp.route('/estimate')
def estimate_pop():
    """
    Get Probability of Profit estimate.

    Query Parameters:
        ticker: Stock symbol
        strike: Option strike price
        expiration: Expiration (YYYYMMDD)
        type: 'CALL' or 'PUT'
        delta: Option delta
        iv: Implied volatility
        dte: Days to expiration
        method: 'delta' or 'monte_carlo'

    Returns:
        JSON with PoP estimate.
    """
    try:
        params = PopEstimateQuery.model_validate(request.args.to_dict())
        if params.option_type not in {'CALL', 'PUT'}:
            return error_response("Invalid option type", status_code=400)
        if params.method not in {'delta', 'monte_carlo'}:
            return error_response("Invalid method", status_code=400)

        result = get_pop(
            params.ticker,
            params.strike,
            params.expiration,
            params.option_type,
            params.delta,
            params.iv,
            params.dte,
            params.method,
        )
        return success_response({'data': result})
    except ValidationError as exc:
        return _validation_error(exc)
    except Exception as e:
        logger.error(f"Error estimating PoP: {e}")
        return error_response(str(e), status_code=500)
