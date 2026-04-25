"""
System API routes

Routes for managing system-level operations including background tasks.
"""

from flask import Blueprint, jsonify
import logging

logger = logging.getLogger('api.routes.system')

bp = Blueprint('system', __name__, url_prefix='/api/system')


@bp.route('/tasks', methods=['GET'])
def get_tasks():
    """
    Get status of all background tasks.

    Returns:
        JSON with task statuses including:
        - name: Task name
        - running: Whether the task thread is alive
        - healthy: Whether the task is healthy
        - restart_count: Number of restart attempts
        - last_start_time: When the task was last started
        - last_error: Most recent error message
    """
    try:
        from app import task_manager
        return jsonify(task_manager.get_all_status())
    except Exception as e:
        logger.error(f"Error getting task status: {e}")
        return jsonify({'error': str(e)}), 500


@bp.route('/tasks/<name>/restart', methods=['POST'])
def restart_task(name):
    """
    Manually restart a background task.

    Args:
        name: Task name (e.g., 'earnings_updater')

    Returns:
        JSON with success status
    """
    try:
        from app import task_manager
        success = task_manager.restart(name)
        return jsonify({'success': success, 'task': name})
    except Exception as e:
        logger.error(f"Error restarting task {name}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/tasks/<name>/status', methods=['GET'])
def get_task_status(name):
    """
    Get status of a specific background task.

    Args:
        name: Task name (e.g., 'earnings_updater')

    Returns:
        JSON with task status
    """
    try:
        from app import task_manager
        status = task_manager.get_status(name)
        if status is None:
            return jsonify({'error': f'Task {name} not found'}), 404
        return jsonify(status)
    except Exception as e:
        logger.error(f"Error getting task {name} status: {e}")
        return jsonify({'error': str(e)}), 500
