const LAST_VISIT_KEY = 'holdings:lastVisitAt';

function appendCell(row, value) {
    const cell = document.createElement('td');
    cell.textContent = value === null || value === undefined || value === '' ? '—' : String(value);
    row.appendChild(cell);
}

export function renderHoldings(payload) {
    const panel = document.getElementById('holdings-panel');
    if (!panel) return;
    const body = document.getElementById('holdings-body');
    const status = document.getElementById('holdings-status');
    const summary = document.getElementById('holdings-summary');
    const changes = document.getElementById('holdings-changes');
    if (!body || !status || !summary || !changes) return;

    body.replaceChildren();
    if (!payload?.available) {
        status.textContent = 'No local portfolio snapshot for the selected account yet.';
        return;
    }
    status.textContent = `Snapshot: ${payload.snapshot_captured_at || 'unknown time'} · local SQLite`;
    summary.textContent = payload.fill_history_window_warning
        ? 'Fill history window is not recorded; adjusted basis stays unproven.'
        : `Dead-money share of NAV: ${payload.dead_money_share_of_nav === null ? '—' : `${(payload.dead_money_share_of_nav * 100).toFixed(1)}%`} · Fill evidence age: ${payload.fill_ingest_age_seconds === null ? 'unknown' : `${Math.floor(payload.fill_ingest_age_seconds / 3600)}h`}`;

    const holdings = payload.holdings || [];
    if (holdings.length === 0) {
        const row = document.createElement('tr');
        const cell = document.createElement('td');
        cell.colSpan = 8;
        cell.className = 'text-muted';
        cell.textContent = 'No stock holdings in the latest snapshot.';
        row.appendChild(cell);
        body.appendChild(row);
    }
    for (const holding of holdings) {
        const row = document.createElement('tr');
        appendCell(row, holding.ticker);
        appendCell(row, holding.shares);
        appendCell(row, holding.broker_sellable_shares === null ? 'unavailable' : holding.broker_sellable_shares);
        appendCell(row, holding.broker_avg_cost === null ? '—' : `${holding.broker_avg_cost} (${holding.basis_source || 'unknown'})`);
        appendCell(row, holding.adjusted_basis === null ? '—' : holding.adjusted_basis);
        appendCell(row, holding.cycle_state || 'unclassified');
        appendCell(row, (holding.open_legs || []).map((leg) => `${leg.option_type} ${leg.strike} ${leg.expiration}`).join('; '));
        const best = holding.best_call_at_or_above_basis;
        appendCell(row, best ? `${best.strike} ${best.expiration}` : '—');
        body.appendChild(row);
    }

    changes.replaceChildren();
    const lines = [];
    for (const expiry of payload.expiries_next_7_days || []) {
        lines.push(`Expiry soon: ${expiry.ticker || '?'} on ${expiry.expiration} (${expiry.days}d)`);
    }
    for (const change of payload.changes || []) {
        lines.push(`${change.timestamp || ''} · ${change.event_type || 'cycle update'} · ${change.ticker || '?'}`);
    }
    if (lines.length === 0) lines.push('No changes since your last visit; no near expiries.');
    for (const line of lines) {
        const item = document.createElement('div');
        item.textContent = line;
        changes.appendChild(item);
    }
}

export async function loadHoldings() {
    const status = document.getElementById('holdings-status');
    if (!document.getElementById('holdings-panel')) return;
    const previousVisit = localStorage.getItem(LAST_VISIT_KEY);
    const query = previousVisit ? `?since=${encodeURIComponent(previousVisit)}` : '';
    try {
        const response = await fetch(`/api/portfolio/holdings${query}`);
        if (!response.ok) throw new Error(`Holdings request failed (${response.status})`);
        renderHoldings(await response.json());
        localStorage.setItem(LAST_VISIT_KEY, new Date().toISOString());
    } catch (error) {
        if (status) status.textContent = error?.message || 'Could not load local holdings.';
    }
    const button = document.getElementById('refresh-holdings');
    if (button && !button.dataset.bound) {
        button.dataset.bound = 'true';
        button.addEventListener('click', loadHoldings);
    }
}
