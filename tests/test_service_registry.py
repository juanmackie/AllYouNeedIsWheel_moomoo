"""
Tests for the lazy service registry (app-scoped instances + serialized construction).

Services are lazily built by ``api.get_service``. Instances must be scoped to the
Flask application that built them (two apps with different configs/databases must
never share a service) and lazy construction must be serialized so concurrent first
access builds a single instance rather than redundant duplicates.
"""

import threading
import unittest
from unittest.mock import patch

import api
from api import create_app, get_service


class _Counter:
    """Deterministic construction probe: each new instance gets a unique uid."""

    _seq = 0

    def __init__(self):
        type(self)._seq += 1
        self.uid = type(self)._seq


class TestServiceRegistry(unittest.TestCase):
    def setUp(self):
        # Register a lightweight deterministic factory under a unique name so the
        # test exercises the registry mechanics without building real services.
        api._service_registry["_s08counter"] = _Counter

    def tearDown(self):
        api._service_registry.pop("_s08counter", None)
        api.clear_service_cache()

    @staticmethod
    def _app(database):
        # Real service factories are NOT registered; create_app only maps config.
        with patch("api._register_services"):
            return create_app(
                {
                    "TESTING": True,
                    "connection_config": {"host": "127.0.0.1", "port": 11111},
                    "database": database,
                }
            )

    def test_instances_are_scoped_per_app(self):
        app_a = self._app("db_a")
        app_b = self._app("db_b")

        with app_a.app_context():
            a1 = get_service("_s08counter")
            a2 = get_service("_s08counter")  # cached within app A
        with app_b.app_context():
            b1 = get_service("_s08counter")
            b2 = get_service("_s08counter")  # cached within app B

        # Each app sees its own singleton across repeated access.
        self.assertIs(a1, a2)
        self.assertIs(b1, b2)
        # Two apps must NOT share a service instantiated under another app's context.
        self.assertIsNot(a1, b1)

    def test_concurrent_first_access_constructs_single_instance(self):
        app = self._app("db_c")
        results = []
        barrier = threading.Barrier(8)
        errors = []

        def access():
            try:
                with app.app_context():
                    barrier.wait()
                    results.append(get_service("_s08counter"))
            except Exception as exc:  # pragma: no cover - failure diagnostics only
                errors.append(exc)

        threads = [threading.Thread(target=access) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        self.assertEqual(len(results), 8)
        # All threads resolved the same app-scoped singleton (serialized build).
        first = results[0]
        for result in results:
            self.assertIs(result, first)
