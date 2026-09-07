"""
S01 — loopback HTTP trust boundary.

Ensures the single-user loopback app rejects cross-origin state-changing requests
and invalid Host headers while preserving deliberate local clients (curl, Python,
same-origin browser fetches).
"""

import unittest
from unittest.mock import MagicMock, patch

from api import is_loopback_host, is_loopback_origin


class TestLoopbackHost(unittest.TestCase):
    """Unit coverage for the Host-header validator."""

    def test_accepts_loopback_hosts(self):
        for host in (
            "localhost",
            "localhost:8000",
            "127.0.0.1",
            "127.0.0.1:8000",
            "[::1]",
            "[::1]:8000",
            "127.0.0.2:9000",
        ):
            with self.subTest(host=host):
                self.assertTrue(is_loopback_host(host), host)

    def test_rejects_foreign_hosts(self):
        for host in (
            "untrusted.example",
            "example.com:8000",
            "evil.com",
            "127.0.0.1.evil.com",
            "10.0.0.5",
            "not-a-host",
        ):
            with self.subTest(host=host):
                self.assertFalse(is_loopback_host(host), host)


class TestLoopbackOrigin(unittest.TestCase):
    """Unit coverage for the Origin-header validator."""

    def test_accepts_loopback_origins(self):
        for origin in ("http://localhost:8000", "http://127.0.0.1:8000", "https://localhost", "http://[::1]:8000"):
            with self.subTest(origin=origin):
                self.assertTrue(is_loopback_origin(origin))

    def test_rejects_foreign_and_malformed_origins(self):
        for origin in (
            "https://untrusted.example",
            "http://evil.com:8000",
            "null",
            "file:///tmp/x",
            "ftp://127.0.0.1",
            "",
        ):
            with self.subTest(origin=origin):
                self.assertFalse(is_loopback_origin(origin), origin)


class TestLoopbackTrustBoundaryRoutes(unittest.TestCase):
    """Route-level enforcement for the state-changing refresh endpoint."""

    def setUp(self):
        from api import create_app

        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    @patch("api.routes.run._get_runner")
    @patch("api.routes.run.start_background_refresh", return_value=True)
    def test_post_with_foreign_origin_rejected(self, mock_refresh, mock_get_runner):
        mock_get_runner.return_value = MagicMock()
        response = self.client.post(
            "/api/run/refresh",
            headers={"Origin": "https://untrusted.example"},
        )
        self.assertEqual(response.status_code, 403)
        data = response.get_json()
        self.assertEqual(data["error_code"], "loopback_trust_boundary")
        self.assertEqual(data["reason"], "disallowed_origin")
        mock_refresh.assert_not_called()

    @patch("api.routes.run._get_runner")
    @patch("api.routes.run.start_background_refresh", return_value=True)
    def test_post_with_invalid_host_rejected(self, mock_refresh, mock_get_runner):
        mock_get_runner.return_value = MagicMock()
        response = self.client.post(
            "/api/run/refresh",
            headers={"Host": "untrusted.example"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["reason"], "invalid_host")
        mock_refresh.assert_not_called()

    @patch("api.routes.run._get_runner")
    @patch("api.routes.run.start_background_refresh", return_value=True)
    def test_post_loopback_origin_allowed(self, mock_refresh, mock_get_runner):
        mock_get_runner.return_value = MagicMock()
        response = self.client.post(
            "/api/run/refresh",
            headers={"Origin": "http://127.0.0.1:8000"},
        )
        self.assertEqual(response.status_code, 202)
        mock_refresh.assert_called_once()

    @patch("api.routes.run._get_runner")
    @patch("api.routes.run.start_background_refresh", return_value=True)
    def test_post_nonbrowser_client_without_origin_allowed(self, mock_refresh, mock_get_runner):
        # curl / Python requests send no Origin header; they are preserved.
        mock_get_runner.return_value = MagicMock()
        response = self.client.post("/api/run/refresh")
        self.assertEqual(response.status_code, 202)
        mock_refresh.assert_called_once()

    def test_get_read_only_route_unaffected(self):
        # GET (no Origin, loopback host) passes through the guard.
        response = self.client.get("/api/run")
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
