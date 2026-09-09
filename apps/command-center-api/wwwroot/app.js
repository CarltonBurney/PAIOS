(function () {
  // The API is served from this same origin, so a relative base needs no CORS
  // policy and no hard-coded port. Set window.PAIOS_API_BASE before this script
  // loads to point the dashboard at a backend on another host.
  const API = window.PAIOS_API_BASE || '';

  // ---------- Theme toggle ----------
  const toggle = document.querySelector('[data-theme-toggle]');
  const root = document.documentElement;
  let theme = matchMedia('(prefers-color-scheme:dark)').matches ? 'dark' : 'light';
  root.setAttribute('data-theme', theme);
  updateToggleIcon();
  toggle.addEventListener('click', () => {
    theme = theme === 'dark' ? 'light' : 'dark';
    root.setAttribute('data-theme', theme);
    updateToggleIcon();
  });
  function updateToggleIcon() {
    toggle.setAttribute('aria-label', 'Switch to ' + (theme === 'dark' ? 'light' : 'dark') + ' mode');
    toggle.innerHTML = theme === 'dark'
      ? '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="5"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/></svg>'
      : '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>';
  }

  // ---------- Color / badge maps ----------
  const CARD_ACCENT = {
    primary: 'var(--color-primary)',
    gold: 'var(--color-gold)',
    blue: 'var(--color-blue)',
    purple: 'var(--color-purple)',
    success: 'var(--color-success)',
    warning: 'var(--color-warning)',
    neutral: 'var(--color-text-muted)',
  };
  const STATUS_STYLE = {
    active:   { bg: 'var(--color-success-highlight)', fg: 'var(--color-success)' },
    planning: { bg: 'var(--color-gold-highlight)', fg: 'var(--color-gold)' },
    blocked:  { bg: 'color-mix(in oklab, var(--color-error) 18%, transparent)', fg: 'var(--color-error)' },
    ongoing:  { bg: 'var(--color-blue-highlight)', fg: 'var(--color-blue)' },
    paused:   { bg: 'var(--color-surface-offset)', fg: 'var(--color-text-muted)' },
    done:     { bg: 'var(--color-primary-highlight)', fg: 'var(--color-primary)' },
  };

  function timeAgo(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    if (isNaN(d)) return '—';
    const diffMs = Date.now() - d.getTime();
    const mins = Math.floor(diffMs / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    const days = Math.floor(hrs / 24);
    if (days < 30) return `${days}d ago`;
    return d.toLocaleDateString();
  }

  // ---------- State ----------
  let currentStatus = null;
  let pollTimer = null;

  const grid = document.getElementById('project-grid');
  const pollIndicator = document.getElementById('pulse-dot');
  const pollText = document.getElementById('poll-text');
  const btnRefresh = document.getElementById('btn-refresh');
  const logToggle = document.getElementById('log-toggle');
  const logBody = document.getElementById('log-body');

  async function fetchStatus() {
    try {
      const res = await fetch(`${API}/api/status`);
      const data = await res.json();
      currentStatus = data;
      render(data);
      pollIndicator.className = 'pulse-dot ok';
      pollText.textContent = `synced ${timeAgo(data.generated_at)}`;
    } catch (e) {
      pollIndicator.className = 'pulse-dot warn';
      pollText.textContent = 'connection lost — retrying…';
    }
  }

  async function triggerPoll() {
    btnRefresh.classList.add('spinning');
    try {
      const res = await fetch(`${API}/api/poll`, { method: 'POST' });
      const data = await res.json();
      currentStatus = data.status;
      render(data.status);
      pollIndicator.className = data.errors && data.errors.length ? 'pulse-dot warn' : 'pulse-dot ok';
      pollText.textContent = `synced ${timeAgo(data.status.generated_at)}`;
    } catch (e) {
      pollIndicator.className = 'pulse-dot warn';
      pollText.textContent = 'poll failed';
    } finally {
      btnRefresh.classList.remove('spinning');
      fetchLog();
    }
  }

  async function fetchLog() {
    try {
      const res = await fetch(`${API}/api/log`);
      const log = await res.json();
      logBody.innerHTML = log.map(entry => `
        <div class="log-row ${entry.ok ? 'ok' : 'warn'}">
          <span class="log-status">${entry.ok ? '✓' : '!'}</span>
          <span>${new Date(entry.time).toLocaleTimeString()}</span>
          <span>${entry.reason}</span>
          <span>${(entry.errors || []).join('; ')}</span>
        </div>`).join('') || '<div class="log-row"><span>No log entries yet</span></div>';
    } catch (e) { /* ignore */ }
  }

  function renderStats(data) {
    const projects = Object.values(data.projects || {});
    document.getElementById('stat-total').textContent = projects.length;
    const sessionCount = projects.reduce((sum, p) => sum + (p.perplexity?.session_count || 0), 0);
    document.getElementById('stat-sessions').textContent = sessionCount || '—';
    const issues = projects.reduce((sum, p) => sum + (p.github?.total_open_issues || 0), 0);
    document.getElementById('stat-issues').textContent = issues;
    document.getElementById('stat-sync').textContent = timeAgo(data.generated_at);
  }

  function cardTemplate(id, p) {
    const accent = CARD_ACCENT[p.color] || CARD_ACCENT.primary;
    const status = (p.manual?.status || 'ongoing').toLowerCase();
    const st = STATUS_STYLE[status] || STATUS_STYLE.ongoing;
    const pct = p.manual?.percent_complete;
    const hasPct = pct !== null && pct !== undefined && pct !== '';

    const gh = p.github;
    const ghAvailable = gh && gh.available;
    const pplxSessions = (p.perplexity?.recent_sessions || []).slice(0, 3);

    return `
    <article class="card" style="--card-accent:${accent}" data-project-id="${id}">
      <div class="card-header">
        <div class="card-title-row">
          <span class="card-emoji">${p.emoji}</span>
          <div>
            <div class="card-title">${p.name}</div>
            <div class="card-category">${p.category}</div>
          </div>
        </div>
        <span class="status-badge" style="--badge-bg:${st.bg};--badge-fg:${st.fg};background:var(--badge-bg);color:var(--badge-fg)">${status}</span>
      </div>

      <div class="card-phase">${p.manual?.phase ? `<strong>${p.manual.phase}</strong>` : '<span>No phase set</span>'}</div>

      ${hasPct ? `
      <div class="progress-track"><div class="progress-fill" style="width:${pct}%"></div></div>
      ` : ''}

      <div class="card-metrics">
        <div class="metric">
          <span class="metric-value">${p.perplexity?.session_count ?? '—'}</span>
          <span class="metric-label">sessions</span>
        </div>
        <div class="metric">
          <span class="metric-value">${ghAvailable ? (gh.total_open_issues ?? 0) : '—'}</span>
          <span class="metric-label">open issues</span>
        </div>
        <div class="metric">
          <span class="metric-value">${ghAvailable ? timeAgo(gh.last_pushed_at) : '—'}</span>
          <span class="metric-label">last push</span>
        </div>
      </div>

      ${p.manual?.next_action ? `
      <div class="card-next">
        <span class="card-next-label">Next action</span>
        ${p.manual.next_action}
      </div>` : ''}

      ${pplxSessions.length ? `
      <div class="card-recent">
        <div class="card-recent-title">Recent sessions</div>
        ${pplxSessions.map(s => `<div class="recent-item"><span class="recent-dot">●</span>${s.url ? `<a href="${s.url}" target="_blank" rel="noopener">${s.title}</a>` : s.title}</div>`).join('')}
      </div>` : ''}

      ${!p.perplexity?.available ? `<div class="card-error">⚠ Perplexity data unavailable — showing last known state</div>` : ''}
      ${p.github?.repos?.length && !ghAvailable ? `<div class="card-error">⚠ GitHub data unavailable</div>` : ''}

      <div class="card-footer">
        <a class="card-link" href="${p.pplx_project_url}" target="_blank" rel="noopener">Open in Perplexity</a>
        ${ghAvailable ? `<a class="card-link" href="${gh.primary_repo_url}" target="_blank" rel="noopener">GitHub</a>` : ''}
        <button class="card-edit-btn" data-edit="${id}">Edit</button>
      </div>
    </article>`;
  }

  function render(data) {
    renderStats(data);
    const entries = Object.entries(data.projects || {});
    grid.innerHTML = entries.map(([id, p]) => cardTemplate(id, p)).join('');
    grid.querySelectorAll('[data-edit]').forEach(btn => {
      btn.addEventListener('click', () => openModal(btn.dataset.edit));
    });
  }

  // ---------- Modal ----------
  const backdrop = document.getElementById('modal-backdrop');
  const form = document.getElementById('edit-form');
  let editingId = null;

  function openModal(id) {
    editingId = id;
    const p = currentStatus.projects[id];
    document.getElementById('modal-title').textContent = `Edit — ${p.name}`;
    document.getElementById('field-status').value = p.manual?.status || 'ongoing';
    document.getElementById('field-phase').value = p.manual?.phase || '';
    document.getElementById('field-next-action').value = p.manual?.next_action || '';
    document.getElementById('field-percent').value = p.manual?.percent_complete ?? '';
    document.getElementById('field-notes').value = p.manual?.notes || '';
    backdrop.hidden = false;
  }
  function closeModal() { backdrop.hidden = true; editingId = null; }

  document.getElementById('modal-close').addEventListener('click', closeModal);
  document.getElementById('modal-cancel').addEventListener('click', closeModal);
  backdrop.addEventListener('click', (e) => { if (e.target === backdrop) closeModal(); });

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const payload = {
      status: document.getElementById('field-status').value,
      phase: document.getElementById('field-phase').value,
      next_action: document.getElementById('field-next-action').value,
      percent_complete: document.getElementById('field-percent').value === '' ? null : Number(document.getElementById('field-percent').value),
      notes: document.getElementById('field-notes').value,
    };
    await fetch(`${API}/api/manual/${editingId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    closeModal();
    fetchStatus();
  });

  // ---------- Log toggle ----------
  logToggle.addEventListener('click', () => {
    const expanded = logToggle.getAttribute('aria-expanded') === 'true';
    logToggle.setAttribute('aria-expanded', String(!expanded));
    logBody.hidden = expanded;
    if (!expanded) fetchLog();
  });

  // ---------- Wire up ----------
  btnRefresh.addEventListener('click', triggerPoll);

  fetchStatus();
  pollTimer = setInterval(fetchStatus, 20000); // light client-side re-fetch of cached status
})();
