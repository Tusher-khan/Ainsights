/* AInsight — shared frontend utilities. */

const AInsight = (() => {
  'use strict';

  /* ---------------------------------------------------------------- API */

  async function request(path, options = {}) {
    const response = await fetch(path, {
      headers: options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' },
      ...options,
    });
    let payload = null;
    try { payload = await response.json(); } catch (e) { payload = null; }
    if (!response.ok) {
      const message = (payload && payload.message) || 'That request could not be completed.';
      const error = new Error(message);
      error.status = response.status;
      error.payload = payload;
      throw error;
    }
    return payload;
  }

  const api = {
    get: (p) => request(p),
    post: (p, body) => request(p, { method: 'POST', body: JSON.stringify(body || {}) }),
    del: (p) => request(p, { method: 'DELETE' }),
    upload: (p, formData, onProgress) => new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', p);
      xhr.upload.addEventListener('progress', (e) => {
        if (onProgress && e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100));
      });
      xhr.onload = () => {
        let data = null;
        try { data = JSON.parse(xhr.responseText); } catch (e) {}
        if (xhr.status >= 200 && xhr.status < 300) resolve(data);
        else reject(new Error((data && data.message) || 'The upload failed.'));
      };
      xhr.onerror = () => reject(new Error('The upload could not reach the server.'));
      xhr.send(formData);
    }),
  };

  /* ------------------------------------------------------------ format */

  function number(value) {
    if (value === null || value === undefined || value === '') return '—';
    if (typeof value !== 'number') {
      const parsed = Number(value);
      if (Number.isNaN(parsed)) return String(value);
      value = parsed;
    }
    const abs = Math.abs(value);
    if (abs >= 1e9) return (value / 1e9).toFixed(2) + 'B';
    if (abs >= 1e6) return (value / 1e6).toFixed(2) + 'M';
    if (abs >= 1e3) return (value / 1e3).toFixed(2) + 'K';
    return Number.isInteger(value) ? value.toLocaleString() : value.toFixed(2);
  }

  function integer(value) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed.toLocaleString() : '—';
  }

  function duration(ms) {
    if (ms === null || ms === undefined) return '—';
    return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(2)} s`;
  }

  function bytes(value) {
    if (!value) return '—';
    const units = ['B', 'KB', 'MB', 'GB'];
    let index = 0, size = Number(value);
    while (size >= 1024 && index < units.length - 1) { size /= 1024; index += 1; }
    return `${size.toFixed(index ? 1 : 0)} ${units[index]}`;
  }

  function when(iso) {
    if (!iso) return '—';
    const date = new Date(iso.endsWith('Z') ? iso : iso + 'Z');
    if (Number.isNaN(date.getTime())) return iso;
    const seconds = (Date.now() - date.getTime()) / 1000;
    if (seconds < 60) return 'just now';
    if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
    if (seconds < 604800) return `${Math.floor(seconds / 86400)} d ago`;
    return date.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
  }

  function escapeHtml(value) {
    return String(value === null || value === undefined ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  /* ------------------------------------------------------------- toast */

  function toast(message, kind = 'info', timeout = 5000) {
    const stack = document.getElementById('toast-stack');
    if (!stack) return;
    const node = document.createElement('div');
    node.className = `toast ${kind}`;
    node.setAttribute('role', kind === 'error' ? 'alert' : 'status');
    node.textContent = message;
    stack.appendChild(node);
    setTimeout(() => node.remove(), timeout);
  }

  /* ------------------------------------------------------------- table */

  function renderTable(columns, rows, limit = 100) {
    if (!rows || !rows.length) return '<p class="muted small">No rows returned.</p>';
    const cols = columns && columns.length ? columns : Object.keys(rows[0]);
    const numeric = {};
    cols.forEach((c) => { numeric[c] = rows.some((r) => typeof r[c] === 'number'); });
    const head = cols.map((c) => `<th class="${numeric[c] ? 'n' : ''}">${escapeHtml(c)}</th>`).join('');
    const body = rows.slice(0, limit).map((row) => {
      const cells = cols.map((c) => {
        const value = row[c];
        const text = typeof value === 'number' ? value.toLocaleString(undefined, { maximumFractionDigits: 2 }) : escapeHtml(value);
        return `<td class="${numeric[c] ? 'n' : ''}">${text}</td>`;
      }).join('');
      return `<tr>${cells}</tr>`;
    }).join('');
    const more = rows.length > limit ? `<p class="muted small" style="margin-top:.5rem">Showing ${limit} of ${rows.length} rows.</p>` : '';
    return `<div class="table-wrap"><table class="data"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>${more}`;
  }

  /* ------------------------------------------------------------- shell */

  function initTheme() {
    const toggle = document.getElementById('theme-toggle');
    if (!toggle) return;
    toggle.addEventListener('click', () => {
      const current = document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
      const next = current === 'dark' ? 'light' : 'dark';
      document.documentElement.setAttribute('data-theme', next);
      try { localStorage.setItem('ainsight-theme', next); } catch (e) {}
      document.dispatchEvent(new CustomEvent('ainsight:theme', { detail: { theme: next } }));
    });
  }

  function initSidebar() {
    const sidebar = document.getElementById('sidebar');
    const toggle = document.getElementById('sidebar-toggle');
    const drawerToggle = document.getElementById('drawer-toggle');
    const scrim = document.getElementById('drawer-scrim');
    if (!sidebar) return;

    try {
      if (localStorage.getItem('ainsight-sidebar') === 'collapsed') {
        sidebar.classList.add('collapsed');
        if (toggle) toggle.setAttribute('aria-expanded', 'false');
      }
    } catch (e) {}

    if (toggle) {
      toggle.addEventListener('click', () => {
        const collapsed = sidebar.classList.toggle('collapsed');
        toggle.setAttribute('aria-expanded', String(!collapsed));
        toggle.setAttribute('aria-label', collapsed ? 'Expand sidebar' : 'Collapse sidebar');
        try { localStorage.setItem('ainsight-sidebar', collapsed ? 'collapsed' : 'expanded'); } catch (e) {}
      });
    }

    function closeDrawer() {
      sidebar.classList.remove('open');
      if (scrim) scrim.hidden = true;
      if (drawerToggle) drawerToggle.setAttribute('aria-expanded', 'false');
    }
    if (drawerToggle) {
      drawerToggle.addEventListener('click', () => {
        const open = sidebar.classList.toggle('open');
        if (scrim) scrim.hidden = !open;
        drawerToggle.setAttribute('aria-expanded', String(open));
      });
    }
    if (scrim) scrim.addEventListener('click', closeDrawer);
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeDrawer(); });
  }

  async function initModeBadge() {
    const badge = document.getElementById('mode-badge');
    if (!badge) return;
    try {
      const health = await api.get('/api/health');
      if (health.demo_mode) {
        badge.textContent = 'Demo mode';
        badge.className = 'tag tag-warn';
        badge.title = 'Answers are computed locally with the deterministic engine; no external model is called.';
      } else {
        badge.textContent = health.llm_provider;
        badge.className = 'tag tag-accent';
        badge.title = `Language model provider: ${health.llm_provider}`;
      }
    } catch (e) {
      badge.textContent = 'Offline';
      badge.className = 'tag tag-danger';
    }
  }

  async function loadDatasetOptions(selectId, { includeAll = false } = {}) {
    const select = document.getElementById(selectId);
    if (!select) return [];
    try {
      const data = await api.get('/api/datasets');
      const datasets = data.datasets || [];
      const saved = (() => { try { return localStorage.getItem('ainsight-dataset'); } catch (e) { return null; } })();
      select.innerHTML =
        (includeAll ? '<option value="">Let AInsight choose</option>' : '') +
        datasets.map((d) => `<option value="${d.id}">${escapeHtml(d.name)} · ${integer(d.row_count)} rows</option>`).join('');
      if (!datasets.length) select.innerHTML = '<option value="">No datasets yet</option>';
      if (saved && datasets.some((d) => d.id === saved)) select.value = saved;
      select.addEventListener('change', () => {
        try { localStorage.setItem('ainsight-dataset', select.value); } catch (e) {}
      });
      return datasets;
    } catch (e) {
      select.innerHTML = '<option value="">Could not load datasets</option>';
      return [];
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    initTheme();
    initSidebar();
    initModeBadge();
  });

  return { api, number, integer, duration, bytes, when, escapeHtml, toast, renderTable, loadDatasetOptions };
})();
