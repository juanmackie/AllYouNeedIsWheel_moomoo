/**
 * ACTIVE WATCHLIST — page foot provenance.
 *
 * Renders the persisted snapshot's active_watchlist payload:
 *   - the Moomoo group actually scanned (name + status)
 *   - a distinct explanation when the group is missing / empty / unreachable
 *   - the last successful sync timestamp (stamped only on a healthy group)
 *   - per-ticker scan status (scanned / error / skipped)
 *   - the holdings checked for covered calls
 *   - unsupported securities with their explicit reason
 *
 * Legacy config/app additions are archived and never shown here as scanned —
 * this foot is the single place that reflects exactly what was evaluated.
 * The element is optional so panels render independently: when the foot is not
 * on the page this renderer is a no-op (keeps unit tests and non-dashboard
 * views safe).
 *
 * All user-controlled strings are rendered via textContent (element creation,
 * never innerHTML), so hostile group/explanation/symbol text is shown as text
 * and can never inject markup.
 */

const STATUS_BADGE = {
    scanned: 'bg-success',
    error: 'bg-danger',
    skipped: 'bg-warning text-dark',
};

const GROUP_STATUS_TEXT = {
    ok: 'group ok',
    missing_group: 'GROUP MISSING',
    empty_group: 'GROUP EMPTY',
    connection_failed: 'UNREACHABLE',
};

export function renderActiveWatchlist(snapshot) {
    const section = document.getElementById('active-watchlist-foot');
    if (!section) return;

    const renderEl = document.getElementById('active-watchlist-render');
    if (!renderEl) return;

    const syncEl = document.getElementById('active-watchlist-sync');
    const aw = snapshot?.active_watchlist;
    if (!aw) {
        renderEl.textContent = snapshot?.run
            ? 'This run carried no active-watchlist payload.'
            : 'Waiting for a run snapshot…';
        setSyncBadge(syncEl, null);
        return;
    }

    const groupName = aw.group_name || 'unknown group';
    const status = aw.group_status || 'ok';
    const clean = status === 'ok';

    renderEl.innerHTML = '';

    // Group line: name + status badge + explanation when unhealthy.
    const groupLine = document.createElement('div');
    groupLine.className = 'd-flex flex-wrap align-items-center gap-2 mb-2';
    const nameSpan = document.createElement('span');
    nameSpan.className = 'fw-semibold';
    nameSpan.textContent = `scan universe: ${groupName}`;
    const statusBadge = document.createElement('span');
    statusBadge.className = `badge ${clean ? 'bg-success' : 'bg-warning text-dark'}`;
    statusBadge.textContent = GROUP_STATUS_TEXT[status] || status.toUpperCase();
    groupLine.append(nameSpan, statusBadge);
    renderEl.appendChild(groupLine);

    if (!clean && aw.explanation) {
        const why = document.createElement('div');
        why.className = 'alert alert-warning py-1 px-2 mb-2';
        why.setAttribute('role', 'status');
        why.textContent = aw.explanation;
        renderEl.appendChild(why);
    }

    // Per-ticker scan status.
    const tickers = Array.isArray(aw.tickers) ? aw.tickers : [];
    if (tickers.length) {
        const tickRow = document.createElement('div');
        tickRow.className = 'd-flex flex-wrap gap-1 mb-1';
        for (const t of tickers) {
            const chip = document.createElement('span');
            chip.className = `badge ${STATUS_BADGE[t.status] || 'bg-secondary'}`;
            chip.textContent = `${t.symbol} · ${t.status}`;
            tickRow.appendChild(chip);
        }
        renderEl.appendChild(tickRow);
    } else if (clean) {
        renderEl.appendChild(muted('no tickers scanned.'));
    }

    // Holdings checked for covered calls.
    const holdings = Array.isArray(aw.holdings_checked) ? aw.holdings_checked : [];
    renderEl.appendChild(
        muted(`CC holdings checked: ${holdings.length ? holdings.join(', ') : 'none'}`)
    );

    // Unsupported securities with their explicit reason.
    const unsupported = Array.isArray(aw.unsupported) ? aw.unsupported : [];
    for (const u of unsupported) {
        const line = document.createElement('div');
        line.className = 'text-danger-emphasis';
        line.textContent = `${u.symbol} — not scanned: ${u.reason || 'unsupported security'}`;
        renderEl.appendChild(line);
    }

    // A failed group read may still carry a raw fetched_at; the badge must
    // reflect only the last SUCCESSFUL sync (healthy group), never a broken/
    // empty/offline read mislabeled as success.
    const lastSuccess = aw.last_successful_sync || (clean ? aw.fetched_at || null : null);
    setSyncBadge(syncEl, lastSuccess);
}

function muted(text) {
    const el = document.createElement('span');
    el.className = 'me-2';
    el.textContent = text;
    return el;
}

function setSyncBadge(syncEl, lastSync) {
    if (!syncEl) return;
    syncEl.className = `badge ${lastSync ? 'bg-secondary' : 'bg-warning text-dark'}`;
    syncEl.textContent = lastSync
        ? `last sync ${new Date(lastSync).toISOString().slice(0, 19).replace('T', ' ')}Z`
        : 'no successful sync';
}
