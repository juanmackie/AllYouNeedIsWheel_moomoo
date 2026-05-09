"""
Route utility helpers — standardized API response envelopes.

All route endpoints should use these helpers to produce consistent
response shapes across the application.

Error envelope:   {'success': False, 'error': 'message'}
Success envelope: {'success': True,  ...merged(data)...}
"""

from flask import jsonify


def error_response(message, status_code=500, **extra):
    """Return a standardized error JSON response.

    Args:
        message: Human-readable error message string.
        status_code: HTTP status code (default 500).
        **extra: Additional key-value pairs merged into the response body.

    Returns:
        Flask response tuple (Response, status_code).
    """
    body = {'success': False, 'error': message}
    if extra:
        body.update(extra)
    return jsonify(body), status_code


def success_response(data=None, status_code=200):
    """Return a standardized success JSON response.

    Args:
        data: Optional dict to merge into the response body.
              Scalars are wrapped as {'data': value}.
        status_code: HTTP status code (default 200).

    Returns:
        Flask response tuple (Response, status_code).
    """
    body = {'success': True}
    if data is not None:
        if isinstance(data, dict):
            body.update(data)
        else:
            body['data'] = data
    return jsonify(body), status_code
