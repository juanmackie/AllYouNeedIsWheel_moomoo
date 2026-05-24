/**
 * Evaluator Widget — loads and displays evaluator/calibrator/scheduler status.
 */
export async function loadEvaluatorWidget() {
    const loadingEl = document.getElementById('evaluator-loading');
    const contentEl = document.getElementById('evaluator-content');
    const errorEl = document.getElementById('evaluator-error');
    if (!loadingEl) return;

    try {
        const resp = await fetch('/api/options/evaluator/stats');
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        const result = data.data || data;

        loadingEl.classList.add('d-none');
        contentEl.classList.remove('d-none');
        errorEl.classList.add('d-none');

        // Summary stats
        const stats = result.stats || {};
        setText('eval-total', stats.total_recommendations ?? '-');
        setText('eval-resolved', stats.resolved ?? '-');
        setText('eval-assignment-rate', stats.assignment_rate != null ? `${stats.assignment_rate}%` : '-');
        setText('eval-expiry-rate', stats.expiry_rate != null ? `${stats.expiry_rate}%` : '-');

        // Feedback bias summary
        const fb = result.feedback_summary || {};
        const overFactors = (fb.over_predicting || []).map(f => `${f.factor} (×${f.mult})`).join(', ');
        const underFactors = (fb.under_predicting || []).map(f => `${f.factor} (×${f.mult})`).join(', ');
        setText('eval-over-factors', overFactors || 'None');
        setText('eval-under-factors', underFactors || 'None');

        // Calibration summary
        const cal = result.calibration;
        if (cal) {
            const msg = `Cycle ${cal.cycle}, samples=${cal.samples}, loss=${cal.loss}`;
            setText('eval-calibration-msg', msg);
            setText('eval-calibration-loss', cal.loss ?? '-');
        } else {
            setText('eval-calibration-msg', 'Not yet run');
            setText('eval-calibration-loss', '-');
        }

        // Scheduler state
        const sched = result.scheduler || {};
        const schedRunning = sched.running ? '✅ Running' : '❌ Not running';
        setText('eval-scheduler-status', schedRunning);
        setText('eval-last-run', formatLastRun(sched.state, 'evaluator'));
        setText('eval-calibrator-run', formatLastRun(sched.state, 'calibrator'));

    } catch (err) {
        console.error('Evaluator widget error:', err);
        loadingEl.classList.add('d-none');
        contentEl.classList.add('d-none');
        errorEl.classList.remove('d-none');
    }
}

function setText(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
}

function formatLastRun(state, name) {
    if (!state || !state[name]) return 'Never';
    const entry = state[name];
    const lastRun = entry.last_run;
    if (!lastRun) return 'Never';
    try {
        const d = new Date(lastRun);
        const now = new Date();
        const diffMs = now - d;
        const diffMin = Math.floor(diffMs / 60000);
        if (diffMin < 1) return 'Just now';
        if (diffMin < 60) return `${diffMin}m ago`;
        const diffHr = Math.floor(diffMin / 60);
        if (diffHr < 24) return `${diffHr}h ago`;
        return d.toLocaleDateString();
    } catch (e) {
        return lastRun;
    }
}

// Refresh button listener (auto-load moved to dashboard-init lazy diagnostics)
document.getElementById('refresh-evaluator-btn')?.addEventListener('click', loadEvaluatorWidget);
