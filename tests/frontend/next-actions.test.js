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
});
