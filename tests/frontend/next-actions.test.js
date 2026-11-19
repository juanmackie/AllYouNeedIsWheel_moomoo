import { beforeEach, describe, expect, it, vi } from 'vitest';
import { renderNextActions } from '../../frontend/static/js/dashboard/next-actions.js';

const run = {
    run: { run_id: 'run-1', market_state: 'closed', quote_fetched_at: {} },
    next_actions: [
        {
            rank: 1, urgency: 'now', underlying: 'AA', wheel_state: 'SHARES_FREE',
            verdict: 'SELL_REPAIR_CALL', reasons: ['Earnings date unknown'],
            ticket: { kind: 'open', eligibility: { mode: 'staged', reasons: [] }, legs: [
                { side: 'SELL_TO_OPEN', option_type: 'CALL', strike: 45.5, expiration: '20261023', quantity: 1, executable_price: 0.4, limit_target: 0.45 },
            ] },
        },
        { rank: 2, urgency: 'routine', underlying: 'SOFI', verdict: 'WAIT', reasons: ['No qualifying call'] },
        { rank: 3, urgency: 'info', underlying: 'UBER', verdict: 'HOLD', reasons: ['Position is healthy'] },
    ],
};

const flushPromises = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('next actions desk', () => {
    beforeEach(() => {
        document.body.innerHTML = '<div id="next-actions-content"></div>';
    });

    it('renders ranked tickets, source/freshness and signal-only copy status; groups HOLD and WAIT', () => {
        renderNextActions(run);
        const root = document.getElementById('next-actions-content');
        expect(root.textContent).toContain('1. AA');
        expect(root.textContent).toContain('SELL_REPAIR_CALL');
        expect(root.textContent).toContain('Earnings date unknown');
        expect(root.textContent).toContain('Source: Moomoo OpenD');
        expect(root.textContent).toContain('Quote freshness unavailable');
        expect(root.textContent).toContain('Signal only — place manually in Moomoo');
        expect(root.querySelectorAll('.copy-ticket-btn')).toHaveLength(1);
        expect(root.querySelector('.copy-ticket-btn').disabled).toBe(false);
        const noAction = root.querySelector('details');
        expect(noAction.querySelector('summary').textContent).toBe('No action needed (2)');
        expect(noAction.textContent).toContain('No qualifying call');
        expect(noAction.textContent).toContain('Position is healthy');
        expect(root.querySelector('.recommendation-card')).toBeNull();
    });

    it('keeps unqualified tickets review-only and renders API text literally', () => {
        const unsafe = structuredClone(run);
        unsafe.next_actions[0].underlying = '<img src=x onerror=alert(1)>';
        unsafe.next_actions[0].ticket.eligibility = { mode: 'review_only', reasons: ['<script>blocked</script>'] };
        renderNextActions(unsafe);
        expect(document.querySelector('#next-actions-content img')).toBeNull();
        expect(document.querySelector('#next-actions-content script')).toBeNull();
        expect(document.querySelector('.next-action-copy-btn').disabled).toBe(true);
        expect(document.querySelector('.copy-status').textContent).toContain('<script>blocked</script>');
    });

    it('renders a clear no-run state and an empty completed-run state', () => {
        renderNextActions(null);
        expect(document.getElementById('next-actions-content').textContent).toContain('Waiting for a completed run');
        renderNextActions({ run: { run_id: 'empty' }, next_actions: [] });
        expect(document.getElementById('next-actions-content').textContent).toContain('This run has no next actions');
    });

    it.each([
        ['moomoo_avg_cost', 'Assignment P&L vs Moomoo average cost: $5.00'],
        ['premium_adjusted', 'Assignment P&L vs premium-adjusted basis: $5.00'],
        [undefined, 'Assignment P&L vs basis: $5.00'],
    ])('names the assignment P&L basis from basis_source %s', (basisSource, expected) => {
        const snapshot = structuredClone(run);
        snapshot.next_actions[0] = { ...snapshot.next_actions[0], if_called_pnl_vs_basis: 5, basis_source: basisSource };
        renderNextActions(snapshot);
        expect(document.querySelector('#next-actions-content').textContent).toContain(expected);
    });

    it('renders campaign break-even values when the snapshot provides campaign records', () => {
        renderNextActions({
            ...run,
            campaigns: [{
                ticker: 'AA', shares: 100, broker_avg_cost: 70, adjusted_basis: 68.94,
                basis_source: 'premium_adjusted', basis_confidence: 'conservative',
                net_option_premium: 105.84, realized_premium_per_week: 8.6,
                break_even: { market_price: 42.2, gap_per_share: 26.74, gap_total: 2674, weeks_to_no_loss_exit: 311, capital_tied_up: 4220 },
            }],
        });
        const records = document.querySelector('.campaign-records');
        expect(records.textContent).toContain('Campaign record');
        expect(records.textContent).toContain('Premium-adjusted · conservative');
        expect(records.textContent).toContain('$70.00');
        expect(records.textContent).toContain('−$1.06');
        expect(records.textContent).toContain('311 weeks');
        expect(records.textContent).toContain('Assumes a flat share price. Not a forecast.');
    });

    it('answers "what changed" in one line and badges the changed actions', () => {
        const snapshot = structuredClone(run);
        snapshot.next_actions[0].action_id = 'AA:CALL:45.5:20261023';
        snapshot.next_actions[1].action_id = 'SOFI:SHARES_FREE';
        snapshot.changes = {
            first_publication: false,
            previous_published_at: '2026-01-01T00:00:00+00:00',
            new: [],
            changed: [{
                action_id: 'AA:CALL:45.5:20261023',
                underlying: 'AA',
                previous_verdict: 'HOLD',
                labels: ['action changed'],
            }],
            resolved: [],
            unchanged_count: 2,
        };
        renderNextActions(snapshot);
        const summary = document.getElementById('next-actions-changes');
        expect(summary.textContent).toContain('1 changed');
        expect(summary.textContent).toContain('Unchanged: 2');
        expect(summary.textContent).toContain('AA: action changed');
        expect(document.querySelector('[data-change="changed"] .action-change-badge').textContent).toBe('CHANGED');
    });

    it('states an unchanged book in one line instead of asking for a review', () => {
        const snapshot = structuredClone(run);
        snapshot.changes = {
            first_publication: false,
            previous_published_at: '2026-01-01T00:00:00+00:00',
            new: [], changed: [], resolved: [], unchanged_count: 3,
        };
        renderNextActions(snapshot);
        const summary = document.getElementById('next-actions-changes');
        expect(summary.textContent).toContain('No material change');
        expect(summary.textContent).toContain('3 action(s) unchanged');
        expect(document.querySelectorAll('.action-change-badge')).toHaveLength(0);
    });

    it('says so when nothing can be compared yet', () => {
        const snapshot = structuredClone(run);
        snapshot.changes = { first_publication: true, new: [], changed: [], resolved: [], unchanged_count: 0 };
        renderNextActions(snapshot);
        expect(document.getElementById('next-actions-changes').textContent).toContain('First published run');
    });

    it('shows the ranking objective and honest evidence status', async () => {
        vi.stubGlobal('fetch', vi.fn(async () => ({
            ok: true,
            json: async () => ({
                status: 'available',
                ranking: 'capital_velocity_per_day',
                objective: 'executable return on deployed capital per day',
                verdict: 'inconclusive',
                age_days: 12,
                sample: { oos_cohorts: 4, min_oos_cohorts: 8 },
                outcome: { excess_per_capital_day: -0.002 },
                reasons: ['only 4 out-of-sample resolved cohorts (need at least 8)'],
            }),
        })));
        renderNextActions(run);
        await flushPromises();
        const note = document.getElementById('next-actions-evidence');
        expect(note.textContent).toContain('Ranking evidence: inconclusive');
        expect(note.textContent).toContain('4/8 resolved out-of-sample cohorts');
        expect(note.textContent).toContain('simulated, after cost');
        expect(note.textContent).toContain('Ranking in use: executable return on deployed capital per day');
        vi.unstubAllGlobals();
    });

    it('reports missing evidence instead of a track record', async () => {
        vi.stubGlobal('fetch', vi.fn(async () => ({
            ok: true,
            json: async () => ({
                status: 'no_evidence',
                objective: 'executable return on deployed capital per day',
                reasons: ['no replay report has been generated'],
            }),
        })));
        renderNextActions(run);
        await flushPromises();
        const note = document.getElementById('next-actions-evidence');
        expect(note.textContent).toContain('none yet');
        expect(note.textContent).toContain('no replay report has been generated');
        vi.unstubAllGlobals();
    });

    it('says a comparison is impossible when the previous run predates revision tracking', () => {
        const snapshot = structuredClone(run);
        snapshot.changes = {
            comparable: false,
            reason: 'the previous publication predates change tracking',
            new: [], changed: [], resolved: [], unchanged_count: 0,
        };
        renderNextActions(snapshot);
        const summary = document.getElementById('next-actions-changes');
        expect(summary.textContent).toContain('Cannot compare with the previous run');
        expect(summary.textContent).toContain('predates change tracking');
        expect(summary.textContent).toContain('Review the list below in full');
    });
});
