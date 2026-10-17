import { copyEligibility, copyTicket } from './ticket.js';

const SIGNAL_ONLY = 'Signal only — place manually in Moomoo';
const BASIS_REASONS = {
    missing_underlying_ticker: 'The broker holding has no recognized ticker.',
    fill_pull_not_successful_this_refresh: 'Fill history was not verified during this refresh.',
    broker_history_capability_unmeasured: 'Broker expiry and history limits still need verification.',
    stock_fills_do_not_reconcile_position: 'Recorded share fills do not explain all shares currently held.',
    no_fills_in_ledger: 'No fills are recorded for this holding.',
    fees_unknown_on_counted_fills: 'Some recorded option fees are unknown.',
    open_short_positions_unavailable: 'Current short-option holdings are unavailable.',
    open_short_leg_missing_from_ledger: 'A current short option is missing from recorded fills.',
    unverified_expired_or_assigned_short_legs: 'Some option expiries or assignments remain unverified.',
    broker_average_cost_unavailable: 'Moomoo average cost is unavailable.',
    no_positive_shares_held: 'No positive share holding was confirmed.',
    campaign_not_verified: 'The campaign history remains unverified.',
};

function text(parent, tag, value, className = '') {
    const node = document.createElement(tag);
    if (className) node.className = className;
    node.textContent = value == null ? '' : String(value);
    parent.appendChild(node);
    return node;
}

function money(value) {
    const n = Number(value);
    return Number.isFinite(n) ? `$${n.toFixed(2)}` : '—';
}

function basisLabelFor(basisSource) {
    if (basisSource === 'premium_adjusted') return 'premium-adjusted basis';
    if (basisSource === 'moomoo_avg_cost') return 'Moomoo average cost';
    return 'basis';
}

function freshnessFor(snapshot, ticker) {
    const fetched = snapshot?.run?.quote_fetched_at || {};
    const stamp = fetched[ticker] || fetched[String(ticker || '').toUpperCase()];
    const ms = Date.parse(stamp || '');
    if (!Number.isFinite(ms)) return snapshot?.run?.market_state === 'closed'
        ? 'Quote freshness unavailable · market closed'
        : 'Quote freshness unavailable';
    const age = Math.max(0, Math.floor((Date.now() - ms) / 1000));
    return `Moomoo quote fetched ${new Date(ms).toISOString().replace('T', ' ').slice(0, 19)}Z · ${age}s ago`;
}

function normalizeReasons(action) {
    const reasons = Array.isArray(action?.reasons) ? action.reasons : [];
    return reasons.map((reason) => typeof reason === 'string'
        ? reason
        : reason?.text || reason?.reason_text || reason?.reason_code || '').filter(Boolean);
}

function renderCampaigns(root, campaigns, actions) {
    if (!Array.isArray(campaigns) || !campaigns.length) return false;
    const section = document.createElement('section');
    section.className = 'campaign-records mt-4';
    text(section, 'h3', 'Campaign record', 'h3 mb-2');
    const grid = document.createElement('div');
    grid.className = 'd-grid gap-2';
    campaigns.forEach((campaign) => {
        const ticker = String(campaign?.ticker || '—').toUpperCase();
        const card = document.createElement('article');
        card.className = 'campaign-record border p-3';
        const repair = campaign?.break_even?.market_price != null && campaign?.adjusted_basis != null
            && Number(campaign.break_even.market_price) < Number(campaign.adjusted_basis);
        text(card, 'h4', `${ticker} · ${campaign?.shares ?? '—'} shares${repair ? ' · REPAIR' : ''}`, 'h5 mb-2');
        const basisLabel = campaign?.basis_source === 'premium_adjusted' ? 'Premium-adjusted' : 'Moomoo average cost';
        const confidence = campaign?.basis_confidence ? ` · ${campaign.basis_confidence}` : '';
        text(card, 'p', `Basis source: ${basisLabel}${confidence}`, 'small text-muted mb-2');
        if (Array.isArray(campaign?.basis_reasons)) {
            campaign.basis_reasons.forEach((reason) => text(card, 'p', BASIS_REASONS[reason] || reason, 'small text-muted mb-1'));
        }
        const premiumPerShare = campaign?.break_even?.premium_per_share
            ?? (Number(campaign?.shares) > 0 && campaign?.net_option_premium != null
                ? Number(campaign.net_option_premium) / Number(campaign.shares)
                : null);
        const values = [
            ['Moomoo cost', campaign?.broker_avg_cost, '/ share'],
            ['Premium banked', campaign?.basis_source === 'premium_adjusted' ? premiumPerShare : null, '/ share'],
            [campaign?.basis_source === 'premium_adjusted' ? 'Adjusted basis' : 'Basis used', campaign?.adjusted_basis, '/ share'],
            ['Market', campaign?.break_even?.market_price, '/ share'],
            ['Gap to a no-loss exit', campaign?.break_even?.gap_per_share, '/ share'],
            ['Total gap', campaign?.break_even?.gap_total, ' total'],
            ['Your realized call income', campaign?.realized_premium_per_week, '/ week'],
            ['Time at a flat share price', campaign?.break_even?.weeks_to_no_loss_exit, ' weeks'],
            ['Capital tied up', campaign?.break_even?.capital_tied_up, ' total'],
        ];
        const table = document.createElement('dl');
        table.className = 'row small mb-2';
        values.forEach(([label, value, unit]) => {
            if (value == null) return;
            text(table, 'dt', label, 'col-7 col-md-5');
            const rendered = label === 'Time at a flat share price'
                ? `${Number(value).toFixed(0)}${unit}`
                : label === 'Premium banked'
                    ? `${Number(value) >= 0 ? '−' : '+'}${money(Math.abs(Number(value)))}${unit}`
                    : `${money(value)}${unit}`;
            text(table, 'dd', rendered, 'col-5 col-md-7');
        });
        card.appendChild(table);
        const relatedAction = (actions || []).find((action) =>
            String(action?.underlying || '').toUpperCase() === ticker &&
            ['SELL_CALL', 'SELL_REPAIR_CALL', 'WAIT'].includes(String(action?.verdict || '').toUpperCase()),
        );
        if (relatedAction) {
            text(card, 'p', `Quoted call: ${relatedAction.verdict}${normalizeReasons(relatedAction).length ? ` · ${normalizeReasons(relatedAction).join(' · ')}` : ''}`, 'small mb-1');
        } else if (campaign?.break_even?.repair_call_reason) {
            text(card, 'p', `Quoted repair call: ${campaign.break_even.repair_call_reason}`, 'small mb-1');
        }
        if (campaign?.best_csp_opportunity) {
            const best = campaign.best_csp_opportunity;
            const rate = Number(best.return_per_day ?? best.capital_velocity_per_day);
            const rateText = Number.isFinite(rate) ? `${(rate * 100).toFixed(2)}%/day` : '—';
            text(card, 'p', `Best qualifying put: ${best.underlying || best.ticker || ''} · ${rateText}`, 'small mb-1');
        }
        if (campaign?.break_even?.opportunity_cost_per_day != null) {
            text(card, 'p', `Opportunity cost: ${money(campaign.break_even.opportunity_cost_per_day)}/day at the best qualifying put rate`, 'small mb-1');
        }
        text(card, 'p', 'Assumes a flat share price. Not a forecast.', 'small text-muted mb-0');
        grid.appendChild(card);
    });
    section.appendChild(grid);
    root.appendChild(section);
    return true;
}

function makeActionCard(action, snapshot) {
    const ticker = String(action?.underlying || '—').toUpperCase();
    const card = document.createElement('article');
    card.className = 'next-action-card border p-3';
    const head = document.createElement('div');
    head.className = 'd-flex flex-wrap align-items-center gap-2 mb-2';
    text(head, 'strong', `${action?.rank ?? ''}. ${ticker}`, 'me-1');
    text(head, 'span', action?.verdict || 'REVIEW', 'badge bg-secondary');
    if (action?.urgency) text(head, 'span', String(action.urgency).replaceAll('_', ' '), 'badge bg-info text-dark');
    if (action?.wheel_state) text(head, 'span', String(action.wheel_state).replaceAll('_', ' '), 'small text-muted');
    card.appendChild(head);

    const reasons = normalizeReasons(action);
    if (reasons.length) {
        const list = document.createElement('ul');
        list.className = 'small mb-2';
        reasons.forEach((reason) => text(list, 'li', reason));
        card.appendChild(list);
    }

    const ticket = action?.ticket;
    if (ticket && Array.isArray(ticket.legs) && ticket.legs.length) {
        ticket.legs.forEach((leg) => {
            const expiry = String(leg.expiration || '').replace(/-/g, '');
            const ticker = String(leg.ticker || action?.underlying || '—').toUpperCase();
            const target = leg.limit_target == null ? '—' : money(leg.limit_target);
            const executable = leg.executable_price == null ? '—' : money(leg.executable_price);
            const qty = leg.quantity == null ? '—' : leg.quantity;
            text(card, 'p', `${String(leg.side || '').toUpperCase()} ${String(leg.option_type || '').toUpperCase()} ${ticker} ${expiry} ${money(leg.strike)} ×${qty} · limit ${target} · executable ${executable}`, 'small mb-1');
            text(card, 'p', `Source: Moomoo OpenD · ${freshnessFor(snapshot, ticker)}`, 'small text-muted mb-2');
        });
        if (ticket.net_credit != null) {
            const creditLabel = ticket.kind === 'roll'
                ? `Net credit: ${money(ticket.net_credit)}/share`
                : String(action.verdict).toUpperCase() === 'DEPLOY_CASH'
                    ? `Projected credit: ${money(ticket.net_credit)} total`
                    : `Net credit: ${money(ticket.net_credit)}`;
            text(card, 'p', creditLabel, 'small mb-2');
        }
        const lossIfCalled = action.loss_if_called ?? ticket.loss_if_called ?? ticket.legs.find((leg) => leg.loss_if_called != null)?.loss_if_called;
        if (lossIfCalled != null) {
            text(card, 'p', `Loss if called: ${money(lossIfCalled)} /contract`, 'small mb-2');
        }
        if (action.if_called_pnl_vs_basis != null) {
            text(card, 'p', `Assignment P&L vs ${basisLabelFor(action.basis_source)}: ${money(action.if_called_pnl_vs_basis)}`, 'small mb-2');
        }

        const eligibility = copyEligibility({ eligibility: action.eligibility || ticket.eligibility });
        const controls = document.createElement('div');
        controls.className = 'd-flex flex-column align-items-start';
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'btn btn-outline-primary btn-sm copy-ticket-btn next-action-copy-btn';
        button.disabled = !eligibility.canCopy;
        button.textContent = eligibility.staged ? 'Stage manual ticket' : eligibility.canCopy ? 'Copy manual ticket' : 'Review only';
        button.title = eligibility.reasons.join(' · ');
        controls.appendChild(button);
        const status = text(controls, 'div', eligibility.canCopy
            ? 'Ready for copy-time revalidation. ' + SIGNAL_ONLY
            : `Copy blocked: ${eligibility.reasons.join(' · ')}. ${SIGNAL_ONLY}`, 'copy-status small mt-1 text-muted');
        status.setAttribute('role', 'status');
        status.dataset.signalsOnly = 'true';
        if (eligibility.canCopy) {
            button.addEventListener('click', () => copyTicket(action, button, {
                runId: snapshot?.run?.run_id || '',
                onStale: () => window.location.reload(),
            }));
        }
        card.appendChild(controls);
    } else {
        text(card, 'p', SIGNAL_ONLY, 'small text-muted mb-0');
    }
    return card;
}

export function renderNextActions(snapshot) {
    const root = document.getElementById('next-actions-content');
    if (!root) return;
    const actions = Array.isArray(snapshot?.next_actions) ? snapshot.next_actions : [];
    if (!snapshot?.run) {
        root.replaceChildren();
        text(root, 'p', 'Waiting for a completed run.', 'text-muted small mb-0');
        return;
    }
    root.replaceChildren();
    const active = actions.filter((item) => !['HOLD', 'WAIT'].includes(String(item?.verdict || '').toUpperCase()));
    const noAction = actions.filter((item) => ['HOLD', 'WAIT'].includes(String(item?.verdict || '').toUpperCase()));
    if (active.length) {
        const grid = document.createElement('div');
        grid.className = 'd-grid gap-2 mb-3';
        active.forEach((item) => grid.appendChild(makeActionCard(item, snapshot)));
        root.appendChild(grid);
    }
    renderCampaigns(root, snapshot?.campaigns, actions);
    if (noAction.length) {
        const disclosure = document.createElement('details');
        disclosure.className = 'border p-3';
        const summary = text(disclosure, 'summary', `No action needed (${noAction.length})`, 'fw-semibold');
        summary.tabIndex = 0;
        const list = document.createElement('div');
        list.className = 'd-grid gap-2 mt-3';
        noAction.forEach((item) => list.appendChild(makeActionCard(item, snapshot)));
        disclosure.appendChild(list);
        root.appendChild(disclosure);
    }
    if (!actions.length) text(root, 'p', 'This run has no next actions.', 'text-muted small mb-0');
}
