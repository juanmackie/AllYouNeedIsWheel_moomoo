#!/usr/bin/env python3
"""
Auto-Trader API Server
Starts the wheel app as one loopback-only Windows process (waitress).
"""

import argparse
import os
import platform
import shutil
import sys

from dotenv import load_dotenv

from core.logging_config import get_logger

# Load environment variables from .env file
load_dotenv()

# Configure logging
logger = get_logger("autotrader.server", "server")


def ensure_local_connection_config():
    """
    Create a local connection.json from the example file when needed.
    """
    config_name = os.environ.get("CONNECTION_CONFIG", "connection.json")
    config_path = os.path.abspath(config_name)

    if os.path.exists(config_path):
        return

    if os.path.basename(config_path) != "connection.json":
        return

    script_dir = os.path.dirname(os.path.abspath(__file__))
    example_path = os.path.join(script_dir, "connection.json.example")
    if not os.path.exists(example_path):
        return

    try:
        shutil.copyfile(example_path, config_path)
        logger.info(f"Created local connection config at {config_path}")
    except Exception as exc:
        logger.warning(f"Could not create {config_path} from example: {exc}")


def _parse_positive_int(name, value, *, maximum=None):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be an integer")
    if parsed < 1:
        raise ValueError(f"{name} must be at least 1")
    if maximum is not None and parsed > maximum:
        raise ValueError(f"{name} must be at most {maximum}")
    return parsed


def main():
    """Start the API server as one loopback-only Windows process."""
    try:
        # Parse command line arguments
        parser = argparse.ArgumentParser(description="Start the Auto-Trader API server")
        parser.add_argument(
            "--realmoney", action="store_true", help="Use real money trading configuration instead of paper trading"
        )
        args = parser.parse_args()

        # Set environment variable for connection config based on the flag
        # Only override if not already set
        if args.realmoney:
            if "CONNECTION_CONFIG" not in os.environ:
                os.environ["CONNECTION_CONFIG"] = "connection_real.json"
            logger.warning("Using REAL MONEY connection configuration — signals only, no execution")
        elif "CONNECTION_CONFIG" not in os.environ:
            os.environ["CONNECTION_CONFIG"] = "connection.json"
            logger.info("Using paper trading configuration (moomoo SIMULATE)")
        else:
            logger.info(f"Using connection config from environment: {os.environ.get('CONNECTION_CONFIG')}")

        ensure_local_connection_config()

        # Get port from environment variable or use default (changed from 5000 to 8000)
        port = _parse_positive_int("PORT", os.environ.get("PORT", "8000"), maximum=65535)

        # Check if port is available
        import socket

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        result = sock.connect_ex(("127.0.0.1", port))
        sock.close()

        if result == 0:
            logger.error(f"Port {port} is already in use. Please stop the existing process or use a different port.")
            logger.info(f"Try: PORT={port + 1} python3 run_api.py")
            sys.exit(1)

        # Detect operating system
        is_windows = platform.system() == "Windows"

        if is_windows:
            # Windows: single waitress process, loopback only
            logger.info(f"Starting Auto-Trader API server on port {port} with waitress (Windows)")
            try:
                from waitress import serve

                from app import ensure_app

                application = ensure_app()
                serve(application, host="127.0.0.1", port=port, threads=2)
            except ImportError:
                logger.error("Waitress is not installed. Please install it with: pip install waitress")
                sys.exit(1)
        else:
            # Non-Windows fallback: single Flask process, loopback only
            logger.info(f"Starting Auto-Trader API server on port {port} (Flask, loopback)")
            from app import ensure_app

            application = ensure_app()
            application.run(host="127.0.0.1", port=port)

    except Exception as e:
        logger.error(f"Error starting API server: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
