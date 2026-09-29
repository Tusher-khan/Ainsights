/* AInsight — dashboard. */

(() => {
  'use strict';
  const { api, integer, number, duration, when, escapeHtml } = AInsight;

  const GLYPHS = {
    rows: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M3 6h18M3 12h18M3 18h18"/></svg>',
    datasets: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><ellipse cx="12" cy="5.5" rx="8" ry="3"/><path d="M4 5.5v12c0 1.7 3.6 3 8 3s8-1.3 8-3v-12"/></svg>',
    queries: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M21 12a8 8 0 0 1-8 8H7l-4 3v-5.5A8 8 0 1 1 21 12Z"/></svg>',
    success: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="m4 12 5 5L20 6"/></svg>',
    time: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/></svg>',
    check: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="8.5"/><path d="m8.5 12 2.5 2.5 4.5-5"/></svg>',
  };

  function kpi(glyph, label, value, note) {
    return `<div class="card kpi">
      <div class="kpi-top">${glyph}<span class="kpi-label">${label}</span></div>
      <div class="kpi-value">${value}</div>
      <div class="kpi-note">${note}</div>
    </div>`;
  }

  async function load() {
    const box = document.getElementById('kpis');
    try {
      const m = await api.get('/api/metrics');
      box.innerHTML = [
        kpi(GLYPHS.rows, 'Total rows', integer(m.total_rows), 'across every dataset'),
        kpi(GLYPHS.datasets, 'Datasets', integer(m.datasets), m.datasets ? 'ready to query' : 'upload one to begin'),
        kpi(GLYPHS.queries, 'Questions asked', integer(m.queries), 'all time'),
        kpi(GLYPHS.success, 'Success rate', m.success_rate === null ? '—' : `${m.success_rate}%`, 'queries that produced a verified result'),
        kpi(GLYPHS.time, 'Average query time', duration(m.avg_query_ms), 'end to end, including planning'),
        kpi(GLYPHS.check, 'Successful queries', integer(m.successful_queries), m.failed_queries ? `${m.failed_queries} did not complete` : 'none failed'),
      ].join('');

      const activity = document.getElementById('activity');
      if (!m.recent_queries || !m.recent_queries.length) {
        activity.innerHTML = '<div class="empty"><h3>No questions yet</h3><p>Your recent analyses will appear here once you start asking.</p><a class="btn btn-primary" href="/chat">Ask AInsight</a></div>';
      } else {
        activity.innerHTML = m.recent_queries.map((q) => `
          <div class="activity-row">
            <span class="tag ${q.status === 'success' ? 'tag-ready' : 'tag-danger'}"><span class="dot"></span>${q.status === 'success' ? 'ok' : 'failed'}</span>
            <span class="q">${escapeHtml(q.question)}</span>
            <span class="meta">${escapeHtml(q.dataset_name || '—')} · ${duration(q.total_ms)} · ${when(q.created_at)}</span>
          </div>`).join('');
      }
    } catch (e) {
      box.innerHTML = `<div class="card card-pad"><p class="muted">The dashboard could not load: ${escapeHtml(e.message)}</p></div>`;
    }
  }

  document.addEventListener('DOMContentLoaded', load);
})();
