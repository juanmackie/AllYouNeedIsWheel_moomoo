/**
 * Growth panel — equity curve + path-to-5x pace + trade journal stats.
 *
 * Data sources (both read-only):
 * - /api/portfolio/history          → persisted per-run portfolio snapshots
 * - /api/options/analytics/lifecycle → inferred trade events + analytics
 */
import { formatCurrency } from '../utils/formatters.js';
import { createSparkline } from '../utils/sparklines.js';

function _fmtDate(iso) {
    if (!iso) return '';
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return iso.slice(0, 10);
    return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

async function fetchHistory() {
    try {
        const resp = await fetch('/api/portfolio/history?limit=365');
        if (!resp.ok) return null;
        return await resp.json();
    } catch (err) {
        console.error('Growth panel: history fetch failed:', err);
        return null;
    }
}

async function fetchJournal() {
    try {
        const resp = await fetch('/api/options/analytics/lifecycle?limit=200');
        if (!resp.ok) return null;
        return await resp.json();
    } catch (err) {
        console.error('Growth panel: journal fetch failed:', err);
        return null;
    }
}

let mcHorizonDays = 1460;

async function fetchProjection(horizonDays) {
    try {
        const resp = await fetch(`/api/portfolio/projection?horizon_days=${horizonDays}&paths=2000`);
        if (!resp.ok) return null;
        return await resp.json();
    } catch (err) {
        console.error('Growth panel: projection fetch failed:', err);
        return null;
    }
}

function _fmtEtaDays(days) {
    if (days === null || days === undefined || !Number.isFinite(Number(days))) return '—';
    const total = Number(days);
    if (total <= 0) return 'Reached';
    if (total > 365.25 * 40) return '>40y';
    const years = Math.floor(total / 365.25);
    const months = Math.round((total % 365.25) / 30.44);
    return years > 0 ? `~${years}y ${months}m` : `~${months}m`;
}

function renderMcChart(container, bands, targetNav) {
    container.innerHTML = '';
    if (!bands || bands.length < 2) {
        container.textContent = 'Not enough projection data.';
        return;
    }
    const width = Math.min(720, Math.max(280, container.clientWidth || 480));
    const height = 180;
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
    svg.setAttribute('width', '100%');
    svg.setAttribute('height', height);
    svg.setAttribute('preserveAspectRatio', 'none');
    svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', `Monte Carlo NAV fan chart over ${bands[bands.length - 1].day} days`);

    const allValues = [];
    for (const band of bands) {
        allValues.push(band.p10, band.p50, band.p90);
    }
    if (Number.isFinite(targetNav)) allValues.push(targetNav);
    let lo = Math.min(...allValues);
    let hi = Math.max(...allValues);
    if (!Number.isFinite(lo) || !Number.isFinite(hi) || hi <= lo) {
        container.textContent = 'Projection collapsed — no finite values.';
        return;
    }
    const pad = (hi - lo) * 0.05 || 1;
    lo -= pad;
    hi += pad;
    const maxDay = bands[bands.length - 1].day || 1;
    const x = (day) => (day / maxDay) * width;
    const y = (value) => height - 8 - ((value - lo) / (hi - lo)) * (height - 16);

    const upper = bands.map((band) => `${x(band.day).toFixed(1)},${y(band.p90).toFixed(1)}`).join(' ');
    const lower = bands
        .slice()
        .reverse()
        .map((band) => `${x(band.day).toFixed(1)},${y(band.p10).toFixed(1)}`)
        .join(' ');
    const bandPoly = document.createElementNS('http://www.w3.org/2000/svg', 'polygon');
    bandPoly.setAttribute('points', `${upper} ${lower}`);
    bandPoly.setAttribute('fill', '#0d6efd');
    bandPoly.setAttribute('opacity', '0.15');

    const medianLine = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
    medianLine.setAttribute(
        'points',
        bands.map((band) => `${x(band.day).toFixed(1)},${y(band.p50).toFixed(1)}`).join(' '),
    );
    medianLine.setAttribute('fill', 'none');
    medianLine.setAttribute('stroke', '#0d6efd');
    medianLine.setAttribute('stroke-width', '2');

    svg.appendChild(bandPoly);
    svg.appendChild(medianLine);

    if (Number.isFinite(targetNav) && targetNav >= lo && targetNav <= hi) {
        const targetLine = document.createElementNS('http://www.w3.org/2000/svg', 'line');
        targetLine.setAttribute('x1', '0');
        targetLine.setAttribute('x2', String(width));
        targetLine.setAttribute('y1', y(targetNav).toFixed(1));
        targetLine.setAttribute('y2', y(targetNav).toFixed(1));
        targetLine.setAttribute('stroke', '#198754');
        targetLine.setAttribute('stroke-dasharray', '6 4');
        targetLine.setAttribute('stroke-width', '1.5');
        svg.appendChild(targetLine);
    }
    container.appendChild(svg);

    const caption = document.createElement('p');
    caption.className = 'mb-0 mt-1 text-muted small';
    caption.textContent =
        `Blue band P10–P90 · line P50 median · dashed green 5x target · ` +
        `${maxDay}-day horizon`;
    container.appendChild(caption);
}

function renderProjection(payload) {
    const chart = document.getElementById('mc-chart');
    const medianEl = document.getElementById('mc-median');
    const rangeEl = document.getElementById('mc-range');
    const probEl = document.getElementById('mc-prob');
    const etaEl = document.getElementById('mc-eta');
    const metaEl = document.getElementById('mc-meta');
    const fillsEl = document.getElementById('mc-fills-note');
    if (!chart && !medianEl) return; // panel not present in this DOM

    const projection = payload?.projection;
    const fills = payload?.fills;
    if (fillsEl) {
        if (fills) {
            fillsEl.textContent =
                `Broker fills corroboration (option-leg only): ` +
                `${formatCurrency(fills.gross_sell_premium ?? 0)} collected − ` +
                `${formatCurrency(fills.gross_buyback_cost ?? 0)} buybacks − ` +
                `${formatCurrency(fills.fees_known_total ?? 0)} fees across ` +
                `${fills.n_option_fills ?? 0} fills / ${fills.n_contract_identities ?? 0} contracts. ` +
                `Zero-price movements: ${fills.zero_price_movement_count ?? 0}. ` +
                `Full per-trade attribution with unknowns: Outcomes panel.`;
        } else {
            fillsEl.textContent = '';
        }
    }
    if (!projection || (projection.status !== 'ok' && projection.status !== 'reached')) {
        const reason = projection?.reason || 'Projection unavailable.';
        if (chart) chart.textContent = reason;
        if (medianEl) medianEl.textContent = '—';
        if (rangeEl) rangeEl.textContent = '—';
        if (probEl) probEl.textContent = '—';
        if (etaEl) etaEl.textContent = '—';
        if (metaEl) metaEl.textContent = '';
        return;
    }
    if (medianEl) medianEl.textContent = formatCurrency(projection.final?.p50 ?? 0);
    if (rangeEl) {
        rangeEl.textContent =
            `${formatCurrency(projection.final?.p10 ?? 0)} – ${formatCurrency(projection.final?.p90 ?? 0)}`;
    }
    if (probEl) {
        const pct = (Number(projection.prob_target_ever ?? 0) * 100).toFixed(0);
        probEl.textContent = `${pct}%`;
    }
    if (etaEl) etaEl.textContent = _fmtEtaDays(projection.median_eta_days);
    if (chart) renderMcChart(chart, projection.bands, Number(projection.target_nav));
    if (metaEl) {
        const calib = projection.calibration || {};
        const bits = [
            `Calibrated on ${projection.daily_points} daily NAV points over ${projection.elapsed_days} days`,
            `${projection.n_paths} paths · seed ${projection.seed}`,
            calib.annualized_median_growth !== undefined
                ? `median drift ${(Number(calib.annualized_median_growth) * 100).toFixed(1)}%/yr`
                : null,
            calib.annualized_vol !== undefined
                ? `vol ${(Number(calib.annualized_vol) * 100).toFixed(1)}%/yr`
                : null,
        ].filter(Boolean);
        const warningText = (projection.warnings || []).join(' ');
        metaEl.textContent = `${bits.join(' · ')}. ${warningText} Projection only — not a promise.`.trim();
    }
}

function wireMcHorizonButtons() {
    const buttons = document.querySelectorAll('[data-mc-horizon]');
    buttons.forEach((button) => {
        if (button.dataset.mcWired) return;
        button.dataset.mcWired = '1';
        button.addEventListener('click', async () => {
            const next = Number(button.dataset.mcHorizon) || 1460;
            mcHorizonDays = next;
            buttons.forEach((other) => other.classList.toggle('active', other === button));
            const payload = await fetchProjection(mcHorizonDays);
            renderProjection(payload);
        });
    });
    buttons.forEach((button) => {
        button.classList.toggle('active', Number(button.dataset.mcHorizon) === mcHorizonDays);
    });
}

function renderEquityCurve(container, series) {
    container.innerHTML = '';
    const validSeries = (Array.isArray(series) ? series : []).filter((point) =>
        Number.isFinite(Number(point?.net_liquidation)) && Number(point.net_liquidation) > 0,
    );
    if (validSeries.length < 2) {
        container.textContent =
            'Not enough history yet — the curve builds one snapshot per completed run.';
        return;
    }
    const navs = validSeries.map((point) => Number(point.net_liquidation));
    const width = Math.min(720, Math.max(280, container.clientWidth || 480));
    const height = 120;
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
    svg.setAttribute('width', '100%');
    svg.setAttribute('height', height);
    svg.setAttribute('preserveAspectRatio', 'none');
    svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', `Net liquidation across ${navs.length} runs`);

    const min = Math.min(...navs);
    const max = Math.max(...navs);
    const range = max - min || 1;
    const points = navs
        .map((value, index) => {
            const x = (index / (navs.length - 1)) * width;
            const y = height - 8 - ((value - min) / range) * (height - 16);
            return `${x.toFixed(1)},${y.toFixed(1)}`;
        })
        .join(' ');

    const rising = navs[navs.length - 1] >= navs[0];
    const color = rising ? '#198754' : '#dc3545';

    const area = document.createElementNS('http://www.w3.org/2000/svg', 'polygon');
    area.setAttribute('points', `0,${height} ${points} ${width},${height}`);
    area.setAttribute('fill', color);
    area.setAttribute('opacity', '0.12');

    const line = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
    line.setAttribute('points', points);
    line.setAttribute('fill', 'none');
    line.setAttribute('stroke', color);
    line.setAttribute('stroke-width', '2');

    svg.appendChild(area);
    svg.appendChild(line);
    container.appendChild(svg);

    const caption = document.createElement('p');
    caption.className = 'mb-0 mt-1 text-muted small';
    caption.textContent = `${_fmtDate(validSeries[0].captured_at)} → ${_fmtDate(
        validSeries[validSeries.length - 1].captured_at,
    )} · ${validSeries.length} snapshots`;
    container.appendChild(caption);
}

const INVESTMENT_GAP_TEXT = {
    cash_flow_coverage_incomplete: 'Cash-flow history does not yet cover this window, so deposits cannot be separated from returns.',
    undated_capital_movement: 'A deposit or withdrawal has no date, so the window cannot be measured.',
    unparseable_capital_movement_amount: 'A deposit or withdrawal has no readable amount, so the window cannot be measured.',
    unsupported_currency: 'A cash flow in another or unknown currency cannot be combined with a USD account value.',
    nonpositive_capital_base: 'The capital base was not positive after a flow, so a return is undefined.',
    fewer_than_two_nav_observations: 'At least two NAV observations are needed.',
};

function describeInvestmentGaps(investment) {
    const gaps = Array.isArray(investment?.gaps) ? investment.gaps : [];
    const texts = gaps.map((gap) => INVESTMENT_GAP_TEXT[gap] || gap).filter(Boolean);
    return texts.join(' ');
}

function renderPace(payload, series) {
    const badge = document.getElementById('growth-track-badge');
    const navEl = document.getElementById('growth-current-nav');
    const targetEl = document.getElementById('growth-target-nav');
    const startingEl = document.getElementById('growth-starting-nav');
    const contributedEl = document.getElementById('growth-contributed');
    const investmentEl = document.getElementById('growth-investment');
    const investmentNote = document.getElementById('growth-investment-note');
    if (!badge && !navEl && !targetEl) return; // panel not present in this DOM
    // Server-declared target multiple (active preset); falls back to the
    // project-wide 5x goal.
    const targetMultiple = Number(payload?.target_multiple) || 5;
    // S04: render every target label from the server objective, including
    // initial/empty states.
    const headingEl = document.getElementById('growth-heading');
    if (headingEl) headingEl.textContent = `Path to ${targetMultiple}x`;
    const validSeries = (Array.isArray(series) ? series : []).filter((point) =>
        Number.isFinite(Number(point?.net_liquidation)) && Number(point.net_liquidation) > 0,
    );
    const latest = validSeries.length ? validSeries[validSeries.length - 1] : null;

    if (!latest) {
        if (badge) {
            badge.textContent = payload ? 'NO HISTORY' : 'HISTORY UNAVAILABLE';
            badge.className = 'badge bg-secondary';
        }
        for (const id of ['growth-current-nav', 'growth-target-nav', 'growth-progress',
            'growth-starting-nav', 'growth-contributed', 'growth-investment']) {
            const el = document.getElementById(id);
            if (el) {
                el.textContent = '—';
                el.classList.remove('text-success', 'text-danger');
            }
        }
        if (investmentNote) investmentNote.textContent = '';
        const bar = document.getElementById('growth-progress-bar');
        if (bar) {
            bar.style.width = '0%';
            bar.setAttribute('aria-valuenow', '0');
        }
        return;
    }

    const currentNav = Number(latest.net_liquidation);
    // C07/S04: render the server's durable target and status — never recompute a
    // moving target from current NAV on the client. C13: the two measures are
    // separate; a reached SIZE milestone says nothing about performance.
    const serverPace = payload?.pace || {};
    const investment = payload?.investment || {};
    const targetNav = Number(serverPace.target_nav);
    const startingNav = Number(serverPace.baseline_nav);
    const reached = Boolean(serverPace.reached);
    if (navEl) navEl.textContent = formatCurrency(currentNav);
    if (targetEl) targetEl.textContent = Number.isFinite(targetNav) ? formatCurrency(targetNav) : '—';
    if (startingEl) startingEl.textContent = Number.isFinite(startingNav) ? formatCurrency(startingNav) : '—';
    const targetLabel = document.getElementById('growth-target-label');
    if (targetLabel) targetLabel.textContent = `${targetMultiple}x`;

    const contributed = investment?.contributed;
    if (contributedEl) {
        if (contributed && Number.isFinite(Number(contributed.net))) {
            const amount = formatCurrency(Math.abs(Number(contributed.net)));
            const sign = Number(contributed.net) < 0 ? '−' : '+';
            contributedEl.textContent = `${sign}${amount}${contributed.complete === false ? '*' : ''}`;
            contributedEl.classList.toggle('text-muted', contributed.complete === false);
        } else {
            contributedEl.textContent = '—';
        }
    }

    const multiple = investment?.investment_multiple;
    const measured = investment?.status === 'measured' && Number.isFinite(Number(multiple));
    if (investmentEl) {
        investmentEl.classList.remove('text-success', 'text-danger', 'text-muted');
        if (measured) {
            investmentEl.textContent = `${Number(multiple).toFixed(2)}×`;
            investmentEl.classList.add(Number(multiple) >= 1 ? 'text-success' : 'text-danger');
        } else {
            investmentEl.textContent = 'unknown';
            investmentEl.classList.add('text-muted');
        }
    }
    if (investmentNote) {
        const gaps = describeInvestmentGaps(investment);
        if (measured) {
            const pct = Number(investment.investment_return_pct);
            const since = investment?.since_last_capital_movement;
            const sinceText = since && since.verified
                ? ` Since the last capital movement: ${Number(since.change_pct).toFixed(2)}%.`
                : '';
            investmentNote.textContent =
                `Flow-adjusted return ${Number.isFinite(pct) && pct > 0 ? '+' : ''}${Number.isFinite(pct) ? pct.toFixed(2) : '0.00'}% ` +
                `over ${investment.window?.days ?? '?'} days.${sinceText}` +
                (contributed?.complete === false ? ' * Contributions are a lower bound; cash-flow coverage is incomplete.' : '');
        } else {
            investmentNote.textContent = gaps
                ? `Investment growth is unknown. ${gaps}${contributed?.complete === false ? ' *' : ''}`
                : 'Investment growth is unknown; there is no measured window yet.';
        }
    }

    const progressPct = Number(serverPace.progress_pct) || 0;
    const progressEl = document.getElementById('growth-progress');
    const barEl = document.getElementById('growth-progress-bar');
    if (progressEl) {
        progressEl.textContent = `${Math.max(0, Math.min(progressPct, 100)).toFixed(1)}% of the way to ${targetMultiple}x account size`;
    }
    if (barEl) {
        barEl.style.width = `${Math.max(0, Math.min(progressPct, 100)).toFixed(1)}%`;
        barEl.setAttribute('aria-valuenow', progressPct.toFixed(1));
    }

    if (badge) {
        if (measured) {
            const up = Number(multiple) >= 1;
            badge.textContent = up ? 'INVESTMENT GROWTH MEASURED' : 'INVESTMENT DOWN';
            badge.className = up ? 'badge bg-success' : 'badge bg-warning text-dark';
        } else if (reached) {
            badge.textContent = 'SIZE TARGET REACHED';
            badge.className = 'badge bg-success';
        } else {
            badge.textContent = 'PERFORMANCE UNKNOWN';
            badge.className = 'badge bg-secondary';
        }
    }
}

function renderJournal(payload) {
    const winRateEl = document.getElementById('journal-win-rate');
    if (!winRateEl) return; // panel not present in this DOM
    const analytics = payload && payload.analytics ? payload.analytics : null;
    if (!analytics) {
        for (const id of ['journal-win-rate', 'journal-exits', 'journal-rolls', 'journal-entries']) {
            const el = document.getElementById(id);
            if (el) el.textContent = '—';
        }
        const detail = document.getElementById('journal-win-rate-detail');
        if (detail) detail.textContent = 'Journal unavailable';
        return;
    }
    const winRate = analytics.win_rate;
    const measuredExits = Number(analytics.measured_exit_count ?? analytics.measured_exits ?? 0);
    const totalExits = Number(analytics.total_exits ?? 0);
    const measuredWins = Number(analytics.measured_wins ?? analytics.wins);
    winRateEl.textContent = measuredExits > 0 && Number.isFinite(winRate)
        ? `${winRate.toFixed(0)}%`
        : '—';
    const winRateDetail = document.getElementById('journal-win-rate-detail');
    if (winRateDetail) {
        const count = Number.isFinite(totalExits) ? totalExits : 0;
        winRateDetail.textContent = measuredExits === 0 && count > 0
            ? 'Exit P&L is not inferred; see Outcomes for broker-verified results'
            : `${Number.isFinite(measuredWins) ? measuredWins : 0} wins · ${measuredExits} of ${count} exits measured`;
    }
    const exitsEl = document.getElementById('journal-exits');
    if (exitsEl) exitsEl.textContent = String(analytics.total_exits ?? 0);
    const rollsEl = document.getElementById('journal-rolls');
    if (rollsEl) rollsEl.textContent = String(analytics.roll_count ?? 0);

    let entries = 0;
    for (const symbolStats of analytics.per_symbol || []) {
        entries += Number(symbolStats.entries) || 0;
    }
    const entriesEl = document.getElementById('journal-entries');
    if (entriesEl) entriesEl.textContent = String(entries);
}

export async function renderGrowthPanel() {
    const [historyPayload, journalPayload, projectionPayload] = await Promise.all([
        fetchHistory(),
        fetchJournal(),
        fetchProjection(mcHorizonDays),
    ]);
    const series = historyPayload ? historyPayload.series : [];

    const curveContainer = document.getElementById('growth-equity-curve');
    if (curveContainer) {
        if (historyPayload) renderEquityCurve(curveContainer, series);
        else curveContainer.textContent = 'Could not load saved history. Retry; this does not mean your records were deleted.';
    }
    renderPace(historyPayload, series);
    renderJournal(journalPayload);
    wireMcHorizonButtons();
    renderProjection(projectionPayload);

    const snapshotCount = document.getElementById('journal-snapshots');
    if (snapshotCount) snapshotCount.textContent = historyPayload ? String(historyPayload.count || 0) : '—';
}
