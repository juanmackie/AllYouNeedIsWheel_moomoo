"""Account-scoped campaign reads and stock-position basis enrichment."""

from __future__ import annotations

from core.wheel_campaign import build_campaigns


class CampaignService:
    """Compose persisted fill evidence into read-only campaign records."""

    def __init__(self, database, capability=None):
        self._db = database
        self._capability = capability or {}
        self._refresh_evidence = {"ok": False, "reason": "fill_pull_not_successful_this_refresh"}

    def set_capability(self, capability):
        """Supply account-scoped capability evidence read by the runtime owner."""
        self._capability = dict(capability or {}) if isinstance(capability, dict) else {}

    def set_refresh_evidence(self, result):
        """Set the result of this exact refresh's pull, never a prior success."""
        result = dict(result or {}) if isinstance(result, dict) else {}
        self._refresh_evidence = {"ok": bool(result.get("ok")), "reason": result.get("reason", "")}

    def get_campaigns(self, positions, env, account_id, *, fill_pull_ok=None, open_option_positions=None, as_of=None):
        fills = self._db.get_fills(env=env, account_id=account_id, limit=50000)
        evidence_ok = self._refresh_evidence["ok"] if fill_pull_ok is None else bool(fill_pull_ok)
        configured_capability = self._db.get_history_capability(env, account_id) or self._capability
        capability_measured = bool(
            isinstance(configured_capability, dict)
            and configured_capability.get("measured")
            and configured_capability.get("expired_worthless_supported")
            and configured_capability.get("assignment_pair_supported")
        )
        coverage = self._db.get_history_fill_coverage(env, account_id)
        if coverage.get("status") == "failed":
            evidence_ok = False
        return build_campaigns(
            positions,
            fills,
            fill_pull_ok=evidence_ok,
            capability_measured=capability_measured,
            open_option_positions=open_option_positions,
            as_of=as_of,
        )

    def enrich_positions(
        self, positions, env, account_id, *, fill_pull_ok=None, open_option_positions=None, as_of=None
    ):
        """Copy stock rows and attach basis metadata without changing avg_cost."""
        campaigns = self.get_campaigns(
            positions,
            env,
            account_id,
            fill_pull_ok=fill_pull_ok,
            open_option_positions=open_option_positions,
            as_of=as_of,
        )
        by_ticker = {campaign["ticker"]: campaign for campaign in campaigns}
        enriched = []
        for position in positions or []:
            row = dict(position)
            campaign = by_ticker.get(str(row.get("symbol", "") or "").replace("US.", "").upper())
            if campaign:
                row.update(
                    adjusted_basis=campaign["adjusted_basis"],
                    basis_source=campaign["basis_source"],
                    basis_confidence=campaign["basis_confidence"],
                    basis_reasons=list(campaign["basis_reasons"]),
                )
            enriched.append(row)
        return enriched
