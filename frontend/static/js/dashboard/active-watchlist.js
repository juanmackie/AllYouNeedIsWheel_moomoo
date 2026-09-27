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
    const blockingBanner = document.getElementById('watchlist-blocking-banner');
    const aw = snapshot?.active_watchlist;
    if (!aw) {
        if (blockingBanner) {
            blockingBanner.className = 'alert alert-danger d-none mb-3';
            blockingBanner.textContent = '';
        }
        renderEl.textContent = snapshot?.run
            ? 'This run carried no active-watchlist payload.'
            : 'Waiting for a run snapshot…';
        setSyncBadge(syncEl, null);
        return;
    }

    const groupName = aw.group_name || 'unknown group';
    const status = aw.group_status || 'ok';
    const clean = status === 'ok';

    if (blockingBanner) {
        blockingBanner.className = clean ? 'alert alert-danger d-none mb-3' : 'alert alert-danger mb-3';
        blockingBanner.textContent = clean
            ? ''
            : `${GROUP_STATUS_TEXT[status] || status.toUpperCase()}: ${aw.explanation || 'The CSP watchlist could not be read. No CSP signal is actionable.'}`;
    }

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
    renderGroupPicker(renderEl, aw);

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

function renderGroupPicker(container, aw) {
    const groups = Array.isArray(aw.groups_available)
        ? [...new Set(aw.groups_available.filter((group) => typeof group === 'string' && group.trim()))]
        : [];
    if (!groups.length) return;

    const form = document.createElement('form');
    form.id = 'watchlist-group-form';
    form.className = 'd-flex flex-wrap align-items-center gap-2 mb-2';
    const label = document.createElement('label');
    label.htmlFor = 'watchlist-group-select';
    label.className = 'small fw-semibold';
    label.textContent = 'Moomoo watchlist group';
    const select = document.createElement('select');
    select.id = 'watchlist-group-select';
    select.className = 'form-select form-select-sm w-auto';
    select.setAttribute('aria-label', 'Moomoo watchlist group');
    for (const group of groups) {
        const option = document.createElement('option');
        option.value = group;
        option.textContent = group;
        select.appendChild(option);
    }
    select.value = aw.group_name || '';

    const button = document.createElement('button');
    button.type = 'submit';
    button.className = 'btn btn-outline-secondary btn-sm';
    button.textContent = 'Use group';
    const message = document.createElement('span');
    message.className = 'small text-muted';
    message.setAttribute('role', 'status');

    form.append(label, select, button, message);
    form.addEventListener('submit', async (event) => {
        event.preventDefault();
        button.disabled = true;
        message.textContent = 'Saving…';
        try {
            const response = await fetch('/api/settings/watchlist-group', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ group: select.value }),
            });
            const result = await response.json();
            if (!response.ok || !result.success) throw new Error(result.error || 'Could not save group');
            message.textContent = 'Saved. Applies on the next refresh.';
        } catch (error) {
            message.textContent = error instanceof Error ? error.message : 'Could not save group';
        } finally {
            button.disabled = false;
        }
    });
    container.appendChild(form);
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
