/**
 * E2E fixture-server harness for Playwright.
 *
 * Spawns `tests/e2e/fixture_server.py` (a real Flask app over the real
 * routes with a deterministic stub broker at the service layer) on a chosen
 * port, waits for its /__e2e/ping, and tears it down on close.
 *
 * Each spec file boots its own server on a distinct port so Playwright
 * workers can run in parallel without sharing state.
 */

import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const REPO_ROOT = fileURLToPath(new URL('../..', import.meta.url));
const FIXTURE_SCRIPT = fileURLToPath(new URL('./fixture_server.py', import.meta.url));

async function fetchJson(base, path, { method = 'GET', body } = {}) {
    const res = await fetch(base + path, {
        method,
        headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
        body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    const text = await res.text();
    let json = null;
    try {
        json = JSON.parse(text);
    } catch {
        // keep null
    }
    if (!res.ok) {
        throw new Error(`fixture ${method} ${path} -> ${res.status}: ${text.slice(0, 200)}`);
    }
    return json;
}

async function destroyProcessTree(proc) {
    if (!proc || proc.exitCode !== null) return;
    if (process.platform === 'win32') {
        await new Promise((resolve) => {
            const killer = spawn('taskkill', ['/pid', String(proc.pid), '/T', '/F'], {
                windowsHide: true,
            });
            killer.on('close', resolve);
        });
    } else {
        proc.kill('SIGTERM');
        await new Promise((resolve) => {
            const timer = setTimeout(() => resolve(), 3000);
            proc.once('exit', () => {
                clearTimeout(timer);
                resolve();
            });
        });
    }
}

export async function startFixtureServer(port, { log = false, startTimeoutMs = 60000 } = {}) {
    const proc = spawn('uv', ['run', 'python', FIXTURE_SCRIPT, '--port', String(port)], {
        cwd: REPO_ROOT,
        stdio: ['ignore', 'pipe', 'pipe'],
        windowsHide: true,
    });

    let stdout = '';
    let stderr = '';
    proc.stdout.on('data', (d) => {
        stdout += d;
        if (log) process.stdout.write(d);
    });
    proc.stderr.on('data', (d) => {
        stderr += d;
        if (log) process.stderr.write(d);
    });

    const base = `http://127.0.0.1:${port}`;
    const deadline = Date.now() + startTimeoutMs;
    for (;;) {
        if (proc.exitCode !== null) {
            await destroyProcessTree(proc);
            throw new Error(`fixture server exited early (code ${proc.exitCode}): ${stderr.slice(0, 600)}`);
        }
        try {
            const ping = await fetchJson(base, '/__e2e/ping');
            if (ping && ping.ok) break;
        } catch {
            // not up yet — retry
        }
        if (Date.now() > deadline) {
            await destroyProcessTree(proc);
            throw new Error(`fixture server timed out starting on port ${port}: ${stderr.slice(0, 600)}`);
        }
        await new Promise((resolve) => setTimeout(resolve, 300));
    }

    return {
        base,
        port,
        proc,
        async ping() {
            return fetchJson(base, '/__e2e/ping');
        },
        async control(path, body = {}) {
            return fetchJson(base, path, { method: 'POST', body });
        },
        async getJson(path) {
            return fetchJson(base, path);
        },
        async reset() {
            await this.control('/__e2e/reset');
        },
        async publish(scene, preset) {
            return this.control('/__e2e/publish-run', { scene, ...(preset ? { preset } : {}) });
        },
        async close() {
            await destroyProcessTree(proc);
        },
    };
}

/** Read the process clipboard with a sentinel preloaded, for no-write assertions. */
export async function seedClipboard(page, sentinel = '__SENTINEL__') {
    await page.evaluate((value) => navigator.clipboard.writeText(value), sentinel);
    return sentinel;
}

export async function readClipboard(page) {
    return page.evaluate(() => navigator.clipboard.readText());
}
