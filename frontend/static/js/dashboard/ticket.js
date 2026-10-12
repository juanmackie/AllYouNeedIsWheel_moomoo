/** Shared signals-only text ticket helpers. Every clipboard write is preceded
 * by a read-only check against the currently published Moomoo run.
 */
import { revalidateCopy } from './api-run.js';

export function copyEligibility(rec) {
    const el = rec?.eligibility || {};
    const mode = el.mode === 'live' || el.mode === 'staged' ? el.mode : 'review_only';
    const reasons = Array.isArray(el.reasons) && el.reasons.length
        ? el.reasons.slice()
        : ['candidate is not copy eligible'];
    return { canCopy: mode === 'live' || mode === 'staged', mode, reasons, staged: mode === 'staged' };
}

export function contractFingerprint(rec) {
    const exp = String(rec?.expiration || '').replace(/-/g, '');
    const strike = Number(rec?.strike);
    if (!rec?.ticker || !rec?.option_type || !exp || !Number.isFinite(strike)) return '';
    return [String(rec.ticker).toUpperCase(), String(rec.option_type).toUpperCase(), exp, strike.toFixed(2)].join('|');
}

export function isTaken(rec, takenKeys = new Set()) {
    const key = contractFingerprint(rec);
    return Boolean(key) && takenKeys.has(key);
}

function setButtonLabel(btn, label) {
    btn.replaceChildren(document.createTextNode(label));
}

function setCopyStatus(btn, text, failed = false) {
    const parent = btn?.parentElement;
    if (!parent) return;
    let status = Array.from(parent.children).find((el) => el.classList.contains('copy-status'));
    if (!status) {
        status = document.createElement('div');
        status.className = 'copy-status small mt-1';
        status.setAttribute('role', 'status');
        btn.insertAdjacentElement('afterend', status);
    }
    const visibleText = text && status.dataset.signalsOnly === 'true' && !text.includes('Signal only — place manually in Moomoo')
        ? `${text} · Signal only — place manually in Moomoo`
        : text || '';
    status.textContent = visibleText;
    status.classList.toggle('d-none', !visibleText);
    status.classList.toggle('text-danger', Boolean(visibleText) && failed);
    status.classList.toggle('text-muted', Boolean(visibleText) && !failed);
}

function ticketLegs(rec) {
    if (Array.isArray(rec?.ticket?.legs)) return rec.ticket.legs;
    return [{
        side: 'SELL', option_type: rec?.option_type, strike: rec?.strike,
        expiration: rec?.expiration, quantity: rec?.recommended_contracts,
        executable_price: rec?.bid_premium_per_contract,
        limit_target: rec?.limit_target_per_contract != null
            ? Number(rec.limit_target_per_contract) / 100 : rec?.mid_price,
    }];
}

function formatLeg(leg, defaultTicker) {
    const ticker = String(leg.ticker || defaultTicker || '?').toUpperCase();
    const expiry = String(leg.expiration || '').replace(/-/g, '');
    const strike = Number(leg.strike);
    const price = Number(leg.limit_target ?? leg.executable_price);
    return `${String(leg.side || '').toUpperCase()} ${String(leg.option_type || '').toUpperCase()} ${ticker} ${expiry} ${Number.isFinite(strike) ? strike.toFixed(2) : '?'} x${Number(leg.quantity) || 0} @ $${Number.isFinite(price) ? price.toFixed(2) : '?'}`;
}

export function buildTicketText(rec, { staged = false } = {}) {
    const ticket = rec?.ticket || {};
    const kind = ticket.kind || rec?.kind || 'open';
    const ticker = String(rec?.underlying || rec?.ticker || '?').toUpperCase();
    if (!rec?.ticket && kind === 'open') {
        const wd = rec?.wheel_decision || {};
        const isCall = String(rec?.option_type || wd.option_type || '').toLowerCase() === 'call';
        const optionType = rec?.option_type || wd.option_type || '';
        const expiry = String(rec?.expiration || '').replace(/-/g, '');
        const strike = rec?.strike == null ? '?' : Number(rec.strike).toFixed(2);
        const qty = Number(rec?.recommended_contracts || 0);
        const limit = rec?.limit_target_per_contract != null
            ? (Number(rec.limit_target_per_contract) / 100).toFixed(2)
            : rec?.mid_price != null ? Number(rec.mid_price).toFixed(2) : '?';
        const premium = rec?.bid_premium_per_contract != null
            ? Number(rec.bid_premium_per_contract).toFixed(2)
            : rec?.premium_per_contract != null ? Number(rec.premium_per_contract).toFixed(2) : '?';
        const cashRequired = rec?.cash_required != null
            ? `Cash required: $${Number(rec.cash_required).toFixed(2)}`
            : rec?.strike != null ? `Cash required: $${(Number(rec.strike) * 100).toFixed(2)}` : '';
        const chainSource = rec?.chain_source || wd.chain_source || 'moomoo';
        const freshness = rec?.quote_age_sec != null ? `${Math.floor(Number(rec.quote_age_sec))}s old` : 'unknown age';
        const lines = [
            `${isCall ? 'SELL TO OPEN COVERED CALL' : 'SELL TO OPEN CSP'} — ${ticker}`,
            `${optionType} ${ticker} ${expiry} ${strike} x${qty}`,
            `Limit $${limit} (midpoint, not guaranteed) — executable bid $${premium}/contract, DTE ${rec?.dte ?? '?'}`,
            cashRequired,
            `Source: ${chainSource} (Moomoo) — quote ${freshness}`,
        ];
        if (staged) lines.push('STAGED FOR US MARKET OPEN — premium is the last broker quote, NOT live; verify the live quote before placing.');
        const eventTier = rec?.event_tier || wd.event_tier || '';
        if (eventTier === 'event_unknown') lines.push('EVENT RISK: earnings status unknown — verify the earnings date before placing.');
        else if (eventTier === 'earnings_before_expiry') lines.push('EVENT RISK: earnings before expiry — high risk, confirm before placing.');
        return lines.filter(Boolean).join('\n');
    }
    const verb = kind === 'close' ? 'CLOSE' : kind === 'roll' ? 'ROLL' : 'OPEN';
    const lines = [
        `${verb} MANUAL OPTION TICKET — ${ticker}`,
        ...ticketLegs(rec).map((leg) => formatLeg(leg, ticker)),
    ];
    if (ticket.net_credit != null) lines.push(`Net credit: $${Number(ticket.net_credit).toFixed(2)}${kind === 'roll' ? '/share' : ' total'}`);
    lines.push('Signal only — place manually in Moomoo');
    if (staged) lines.push('STAGED FOR US MARKET OPEN — verify the live quote before placing.');
    const source = rec?.source || rec?.source_label || 'Moomoo';
    const freshness = rec?.freshness || rec?.quote_age_text || rec?.quote_age;
    lines.push(`Source: ${source}${freshness ? ` · freshness ${freshness}` : ''}`);
    return lines.join('\n');
}

/**
 * Copy a text ticket only after the backend revalidates the same run and the
 * requested open/close/roll contract. `onStale` refreshes the visible panel.
 */
export async function copyTicket(rec, btn, { runId = '', onStale = null } = {}) {
    const eligibility = copyEligibility(rec);
    if (!eligibility.canCopy) return;
    const ticket = rec?.ticket || {};
    const legs = ticketLegs(rec);
    const kind = ticket.kind || rec?.kind || 'open';
    const openLeg = kind === 'roll' ? legs.find((leg) => String(leg.side).toUpperCase().includes('SELL')) : legs[0];
    const closeLeg = kind === 'roll' ? legs.find((leg) => String(leg.side).toUpperCase().includes('BUY')) : legs[0];
    const contractLeg = openLeg || closeLeg || {};
    const oldNodes = Array.from(btn.childNodes, (node) => node.cloneNode(true));
    btn.disabled = true;
    setButtonLabel(btn, 'Checking…');
    setCopyStatus(btn, '');
    try {
        const checks = kind === 'roll'
            ? [await revalidateCopy({
                run_id: runId, kind, ticker: openLeg?.ticker || rec?.underlying || rec?.ticker,
                option_type: openLeg?.option_type, expiration: openLeg?.expiration,
                strike: openLeg?.strike, quantity: openLeg?.quantity,
                close_expiration: closeLeg?.expiration, close_strike: closeLeg?.strike,
            })]
            : await Promise.all(legs.map((leg) => revalidateCopy({
                run_id: runId, kind, ticker: leg.ticker || rec?.underlying || rec?.ticker,
                option_type: leg.option_type, expiration: leg.expiration, strike: leg.strike,
                quantity: leg.quantity,
            })));
        const invalid = checks.find((result) =>
            result?.ok !== true || result?.allowed === false ||
            result?.matched_run !== true || result?.matched_contract !== true ||
            !['live', 'staged', 'review_only'].includes(result?.mode) || result?.mode === 'review_only',
        );
        if (invalid) {
            if (invalid.matched_run === false || invalid.matched_contract === false) {
                btn.disabled = true;
                setButtonLabel(btn, 'Review again');
                const message = 'the run or contract changed — refresh and review before copying';
                btn.title = message;
                setCopyStatus(btn, `Copy blocked: ${message}.`, true);
                if (onStale) window.setTimeout(onStale, 0);
                return;
            }
            const reasons = invalid?.reasons || [invalid?.reason || 'copy revalidation blocked this ticket'];
            btn.disabled = true;
            setButtonLabel(btn, 'Review only');
            btn.title = reasons.join(' · ');
            setCopyStatus(btn, `Copy blocked: ${reasons.join(' · ')}`, true);
            return;
        }
        const reval = checks[0];
        if (checks.some((result) => result.mode !== reval.mode)) {
            btn.disabled = true;
            setButtonLabel(btn, 'Review again');
            setCopyStatus(btn, 'Copy blocked: market state differs across ticket lines. Review the current ticket.', true);
            return;
        }
        const mode = reval?.mode || (reval?.staged ? 'staged' : 'live');
        if (mode !== eligibility.mode) {
            btn.disabled = true;
            setButtonLabel(btn, 'Review again');
            const message = `market ${mode === 'staged' ? 'closed' : 'appears open now (fresh broker quotes)'} — refresh for ${mode === 'staged' ? 'staged' : 'live'} copy; staging blocked`;
            btn.title = message;
            setCopyStatus(btn, `Copy blocked: ${message}`, true);
            if (onStale) window.setTimeout(onStale, 0);
            return;
        }
        const refreshed = legs.map((leg, index) => {
            const result = kind === 'roll' ? reval : checks[index];
            const quote = kind === 'roll'
                ? (String(leg.side).toUpperCase().includes('BUY') ? result.close_contract : result.contract)
                : (kind === 'close' ? result.contract : result.contract);
            if (!quote) return leg;
            const isBuy = String(leg.side).toUpperCase().includes('BUY');
            const executable = kind === 'close' || kind === 'roll'
                ? (isBuy ? quote.ask : quote.bid)
                : quote.bid ?? leg.executable_price;
            const limitTarget = kind === 'close' || kind === 'roll'
                ? executable
                : quote.limit_target ?? quote.mid_price ?? quote.mid ?? leg.limit_target;
            return { ...leg, executable_price: executable ?? leg.executable_price, limit_target: limitTarget };
        });
        const current = !rec?.ticket && kind === 'open' && checks[0]?.contract
            ? { ...rec, ...checks[0].contract }
            : !rec?.ticket && kind === 'open'
                ? rec
                : { ...rec, ticket: {
                    ...ticket,
                    legs: refreshed,
                    net_credit: reval.net_credit ?? ticket.net_credit,
                } };
        if (kind === 'open' && String(rec?.underlying || '').toUpperCase() === 'CASH') {
            current.ticket.net_credit = refreshed.reduce((total, leg) =>
                total + Number(leg.executable_price) * Number(leg.quantity) * 100, 0);
        }
        await navigator.clipboard.writeText(buildTicketText(current, { staged: mode === 'staged' }));
        btn.disabled = false;
        btn.classList.add('btn-success');
        setButtonLabel(btn, 'Copied');
        setCopyStatus(btn, 'Copied manual ticket text. Signal only — place manually in Moomoo');
    } catch (err) {
        console.error('Ticket copy failed:', err);
        btn.disabled = true;
        btn.classList.add('btn-danger');
        setButtonLabel(btn, 'Not copied');
        setCopyStatus(btn, 'Copy blocked: revalidation or clipboard access failed. Verify the ticket and try again.', true);
    } finally {
        window.setTimeout(() => {
            if (btn.isConnected && btn.textContent.includes('Copied')) {
                btn.replaceChildren(...oldNodes);
                btn.disabled = !eligibility.canCopy;
                btn.classList.remove('btn-success', 'btn-danger', 'btn-warning');
            }
        }, 2000);
    }
}
