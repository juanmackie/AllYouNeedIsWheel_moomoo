"""
System API routes

Routes for managing system-level operations including background tasks.
"""

from flask import Blueprint, jsonify
import logging
from api.routes.utils import error_response, success_response

logger = logging.getLogger('api.routes.system')

bp = Blueprint('system', __name__, url_prefix='/api/system')


@bp.route('/tasks', methods=['GET'])
def get_tasks():
    try:
        from app import task_manager
        return jsonify(task_manager.get_all_status())
    except Exception as e:
        logger.error(f"Error getting task status: {e}")
        return error_response(str(e))


@bp.route('/tasks/<name>/restart', methods=['POST'])
def restart_task(name):
    try:
        from app import task_manager
        success = task_manager.restart(name)
        return jsonify({'success': success, 'task': name})
    except Exception as e:
        logger.error(f"Error restarting task {name}: {e}")
        return error_response(str(e))


@bp.route('/tasks/<name>/status', methods=['GET'])
def get_task_status(name):
    try:
        from app import task_manager
        status = task_manager.get_status(name)
        if status is None:
            return error_response(f'Task {name} not found', status_code=404)
        return jsonify(status)
    except Exception as e:
        logger.error(f"Error getting task {name} status: {e}")
        return error_response(str(e))
