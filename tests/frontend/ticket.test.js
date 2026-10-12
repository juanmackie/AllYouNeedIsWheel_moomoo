import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../frontend/static/js/dashboard/api-run.js', () => ({
    revalidateCopy: vi.fn(),
}));

import { revalidateCopy } from '../../frontend/static/js/dashboard/api-run.js';
import { copyTicket } from '../../frontend/static/js/dashboard/ticket.js';

const allowed = (contract = {}) => ({
    ok: true, matched_run: true, matched_contract: true, mode: 'staged',
    reasons: [], contract,
});

describe('shared manual ticket revalidation', () => {
    afterEach(() => {
        vi.clearAllMocks();
        vi.unstubAllGlobals();
        document.body.innerHTML = '';
    });

    it('checks every cash-plan line by its own ticker before one clipboard write', async () => {
        const clipboardWrite = vi.fn().mockResolvedValue(undefined);
        vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText: clipboardWrite } });
        document.body.innerHTML = '<div><button id="copy">Copy</button></div>';
        revalidateCopy.mockResolvedValueOnce(allowed({ bid: 1.2, mid_price: 1.3 }))
            .mockResolvedValueOnce(allowed({ bid: 0.8, mid_price: 0.9 }));
        const action = {
            underlying: 'CASH',
            eligibility: { mode: 'staged', reasons: [] },
            ticket: { kind: 'open', legs: [
                { ticker: 'SOXL', side: 'SELL_TO_OPEN', option_type: 'PUT', strike: 30, expiration: '20261120', quantity: 1, limit_target: 1.1 },
                { ticker: 'AAPL', side: 'SELL_TO_OPEN', option_type: 'PUT', strike: 200, expiration: '20261120', quantity: 2, limit_target: 0.7 },
            ] },
        };
        await copyTicket(action, document.getElementById('copy'), { runId: 'run-1' });
        expect(revalidateCopy).toHaveBeenCalledTimes(2);
        expect(revalidateCopy.mock.calls.map(([call]) => call.ticker)).toEqual(['SOXL', 'AAPL']);
        expect(revalidateCopy.mock.calls.map(([call]) => call.run_id)).toEqual(['run-1', 'run-1']);
        expect(clipboardWrite).toHaveBeenCalledTimes(1);
        expect(clipboardWrite.mock.calls[0][0]).toContain('SOXL');
        expect(clipboardWrite.mock.calls[0][0]).toContain('AAPL');
        expect(clipboardWrite.mock.calls[0][0]).toContain('Net credit: $280.00 total');
        expect(clipboardWrite.mock.calls[0][0]).toContain('Signal only — place manually in Moomoo');
    });

    it('uses fresh ask for close and fresh two-leg prices for roll tickets', async () => {
        const clipboardWrite = vi.fn().mockResolvedValue(undefined);
        vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText: clipboardWrite } });
        document.body.innerHTML = '<div><button id="copy">Copy</button></div>';
        revalidateCopy.mockResolvedValueOnce({ ...allowed({ bid: 0.5, ask: 0.62 }), contract: { bid: 0.5, ask: 0.62 } });
        await copyTicket({ underlying: 'SOFI', eligibility: { mode: 'staged' }, ticket: {
            kind: 'close', legs: [{ ticker: 'SOFI', side: 'BUY_TO_CLOSE', option_type: 'CALL', strike: 17.5, expiration: '20261120', quantity: 1, limit_target: 0.1 }],
        } }, document.getElementById('copy'), { runId: 'r' });
        expect(revalidateCopy.mock.calls[0][0]).toMatchObject({ kind: 'close', ticker: 'SOFI', quantity: 1 });
        expect(clipboardWrite.mock.calls[0][0]).toContain('@ $0.62');

        clipboardWrite.mockClear();
        revalidateCopy.mockReset().mockResolvedValueOnce({
            ...allowed({ bid: 1.1 }), contract: { bid: 1.1, ask: 1.2 },
            close_contract: { bid: 0.4, ask: 0.55 }, net_credit: 0.55,
        });
        const roll = {
            underlying: 'SOFI', eligibility: { mode: 'staged' }, ticket: { kind: 'roll', legs: [
                { ticker: 'SOFI', side: 'BUY_TO_CLOSE', option_type: 'CALL', strike: 17.5, expiration: '20261120', quantity: 1, limit_target: 9 },
                { ticker: 'SOFI', side: 'SELL_TO_OPEN', option_type: 'CALL', strike: 18, expiration: '20261218', quantity: 1, limit_target: 0.01 },
            ] },
        };
        await copyTicket(roll, document.getElementById('copy'), { runId: 'r' });
        expect(revalidateCopy.mock.calls[0][0]).toMatchObject({
            kind: 'roll', ticker: 'SOFI', expiration: '20261218', strike: 18,
            close_expiration: '20261120', close_strike: 17.5,
        });
        expect(clipboardWrite.mock.calls[0][0]).toContain('@ $0.55');
        expect(clipboardWrite.mock.calls[0][0]).toContain('@ $1.10');
    });

    it('blocks tickets when the response omits a recognized mode or matched-run proof', async () => {
        const clipboardWrite = vi.fn().mockResolvedValue(undefined);
        vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText: clipboardWrite } });
        document.body.innerHTML = '<div><button id="copy">Copy</button></div>';
        revalidateCopy.mockResolvedValue({ ok: true, contract: {} });
        const btn = document.getElementById('copy');
        await copyTicket({ ticker: 'AAPL', eligibility: { mode: 'staged' } }, btn, { runId: 'r' });
        expect(clipboardWrite).not.toHaveBeenCalled();
        expect(btn.disabled).toBe(true);
    });

    it('blocks the whole cash ticket when any one line fails its copy check', async () => {
        const clipboardWrite = vi.fn().mockResolvedValue(undefined);
        vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText: clipboardWrite } });
        document.body.innerHTML = '<div><button id="copy">Copy</button></div>';
        revalidateCopy.mockResolvedValueOnce(allowed({ bid: 1 }))
            .mockResolvedValueOnce({ ok: true, matched_run: true, matched_contract: false, mode: 'staged', reasons: ['contract left the current lane'] });
        await copyTicket({
            eligibility: { mode: 'staged' },
            ticket: { kind: 'open', legs: [
                { ticker: 'AAPL', side: 'SELL', option_type: 'PUT', strike: 200, expiration: '20261120', quantity: 1 },
                { ticker: 'TSLA', side: 'SELL', option_type: 'PUT', strike: 300, expiration: '20261120', quantity: 1 },
            ] },
        }, document.getElementById('copy'), { runId: 'r' });
        expect(revalidateCopy).toHaveBeenCalledTimes(2);
        expect(clipboardWrite).not.toHaveBeenCalled();
    });
});
