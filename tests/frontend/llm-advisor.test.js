import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

function setupDOM() {
  document.body.innerHTML = `
    <section id="llm-advisor-section" data-llm-enabled="true">
      <button id="llm-generate-btn" disabled>
        <span id="llm-spinner" class="d-none"></span>
        <span id="llm-generate-label">Generate Suggestions</span>
      </button>
      <div id="llm-status"></div>
      <div id="llm-result" class="d-none mt-3">
        <div class="alert alert-info" role="alert">
          <div id="llm-result-text"></div>
        </div>
        <small id="llm-result-meta"></small>
      </div>
      <div id="llm-error" class="d-none mt-3">
        <div class="alert alert-warning" role="alert" id="llm-error-text"></div>
      </div>
    </section>
  `;
}

describe('llm advisor initialization', () => {
  beforeEach(() => {
    setupDOM();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    document.body.innerHTML = '';
  });

  it('disables suggestions without posting when the advisor is unconfigured', async () => {
    const fetchMock = vi.fn(async (url) => {
      if (url === '/api/llm/status') {
        return {
          ok: true,
          status: 200,
          headers: { get: () => 'application/json' },
          json: async () => ({
            success: true,
            enabled: true,
            configured: false,
            available: false,
            message: 'LLM advisor needs LLM_API_KEY before suggestions can run.',
          }),
        };
      }
      throw new Error(`unexpected fetch ${url}`);
    });
    vi.stubGlobal('fetch', fetchMock);

    const { initializeLLMAdvisor } = await import(
      '../../frontend/static/js/dashboard/llm-advisor.js'
    );

    initializeLLMAdvisor();
    await vi.waitFor(() => {
      expect(document.getElementById('llm-status').textContent).toContain('LLM_API_KEY');
    });

    const button = document.getElementById('llm-generate-btn');
    expect(button.disabled).toBe(true);
    button.click();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith('/api/llm/status');
  });

  it('handles an older backend without the status route', async () => {
    const fetchMock = vi.fn(async (url) => {
      if (url === '/api/llm/status') {
        return {
          ok: false,
          status: 404,
          headers: { get: () => 'text/html' },
          json: async () => {
            throw new SyntaxError('Unexpected token <');
          },
        };
      }
      throw new Error(`unexpected fetch ${url}`);
    });
    vi.stubGlobal('fetch', fetchMock);
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});

    const { initializeLLMAdvisor } = await import(
      '../../frontend/static/js/dashboard/llm-advisor.js'
    );

    initializeLLMAdvisor();
    await vi.waitFor(() => {
      expect(document.getElementById('llm-status').textContent).toContain('Restart the local server');
    });

    expect(document.getElementById('llm-generate-btn').disabled).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(warnSpy).not.toHaveBeenCalled();
    warnSpy.mockRestore();
  });

  it('shows an error instead of a blank successful result when the provider returns empty text', async () => {
    const fetchMock = vi.fn(async (url) => {
      if (url === '/api/llm/status') {
        return {
          ok: true,
          status: 200,
          headers: { get: () => 'application/json' },
          json: async () => ({
            success: true,
            enabled: true,
            configured: true,
            available: true,
            message: 'ready',
          }),
        };
      }
      if (url === '/api/llm/suggestions') {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            success: true,
            text: '   ',
            provider: 'openrouter',
            model: 'nex-agi/nex-n2-pro:free',
          }),
        };
      }
      throw new Error(`unexpected fetch ${url}`);
    });
    vi.stubGlobal('fetch', fetchMock);

    const { initializeLLMAdvisor } = await import(
      '../../frontend/static/js/dashboard/llm-advisor.js'
    );

    initializeLLMAdvisor();
    await vi.waitFor(() => {
      expect(document.getElementById('llm-generate-btn').disabled).toBe(false);
    });

    document.getElementById('llm-generate-btn').click();
    await vi.waitFor(() => {
      expect(document.getElementById('llm-error').classList.contains('d-none')).toBe(false);
    });

    expect(document.getElementById('llm-error-text').textContent).toContain('empty response');
    expect(document.querySelector('#llm-error .alert')?.classList.contains('show')).toBe(true);
    expect(document.getElementById('llm-result').classList.contains('d-none')).toBe(true);
    expect(document.getElementById('llm-status').textContent).toBe('No advisor output returned.');
    expect(document.getElementById('llm-generate-label').textContent).toBe('Generate Suggestions');
    expect(document.getElementById('llm-generate-btn').textContent).not.toContain('Generate Suggestions Generate Suggestions');
  });

  it('shows the advisor result alert when the provider returns content', async () => {
    const fetchMock = vi.fn(async (url) => {
      if (url === '/api/llm/status') {
        return {
          ok: true,
          status: 200,
          headers: { get: () => 'application/json' },
          json: async () => ({
            success: true,
            enabled: true,
            configured: true,
            available: true,
            message: 'ready',
          }),
        };
      }
      if (url === '/api/llm/suggestions') {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            success: true,
            text: '## Research note\n- Watch NVDA',
            provider: 'openrouter',
            model: 'nex-agi/nex-n2-pro:free',
          }),
        };
      }
      throw new Error(`unexpected fetch ${url}`);
    });
    vi.stubGlobal('fetch', fetchMock);

    const { initializeLLMAdvisor } = await import(
      '../../frontend/static/js/dashboard/llm-advisor.js'
    );

    initializeLLMAdvisor();
    await vi.waitFor(() => {
      expect(document.getElementById('llm-generate-btn').disabled).toBe(false);
    });

    document.getElementById('llm-generate-btn').click();
    await vi.waitFor(() => {
      expect(document.getElementById('llm-result').classList.contains('d-none')).toBe(false);
    });

    expect(document.querySelector('#llm-result .alert')?.classList.contains('show')).toBe(true);
    expect(document.getElementById('llm-result-meta').textContent).toContain('openrouter');
    expect(document.getElementById('llm-result-text').innerHTML).toContain('Research note');
  });
});
