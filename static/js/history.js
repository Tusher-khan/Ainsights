/* AInsight — query history. */

(() => {
  'use strict';
  const { api, duration, when, escapeHtml, toast, integer } = AInsight;
  const list = document.getElementById('history-list');

  function row(entry) {
    return `
      <div class="activity-row" data-id="${entry.id}">
        <span class="tag ${entry.status === 'success' ? 'tag-ready' : 'tag-danger'}"><span class="dot"></span>${escapeHtml(entry.status || '—')}</span>
        <span class="q" title="${escapeHtml(entry.question)}">${escapeHtml(entry.question)}</span>
        <span class="meta">${escapeHtml(entry.dataset_name || '—')} · ${escapeHtml(entry.intent || '—')} · ${integer(entry.row_count)} rows · ${duration(entry.total_ms)} · ${when(entry.created_at)}</span>
        <button class="btn btn-sm" data-action="rerun">Run again</button>
        <button class="btn btn-sm btn-ghost" data-action="delete" aria-label="Delete this entry">✕</button>
      </div>`;
  }

  async function load() {
    list.innerHTML = '<div class="card-pad"><div class="skeleton" style="width:40%"></div></div>';
    try {
      const data = await api.get('/api/history?limit=200');
      const history = data.history || [];
      if (!history.length) {
        list.innerHTML = '<div class="empty"><h3>No queries yet</h3><p>Ask AInsight a question and it will be recorded here.</p><a class="btn btn-primary" href="/chat">Ask AInsight</a></div>';
        return;
      }
      list.innerHTML = history.map(row).join('');
      list.querySelectorAll('[data-action]').forEach((button) => {
        button.addEventListener('click', async () => {
          const node = button.closest('.activity-row');
          const id = node.dataset.id;
          if (button.dataset.action === 'delete') {
            try { await api.del(`/api/history/${id}`); node.remove(); } catch (e) { toast(e.message, 'error'); }
          } else {
            const question = node.querySelector('.q').textContent;
            window.location.href = `/chat?q=${encodeURIComponent(question)}`;
          }
        });
      });
    } catch (e) {
      list.innerHTML = `<div class="card-pad"><p class="muted">History could not be loaded: ${escapeHtml(e.message)}</p></div>`;
    }
  }

  const clear = document.getElementById('clear-history');
  if (clear) clear.addEventListener('click', async () => {
    if (!window.confirm('Delete every entry in your query history?')) return;
    try { await api.del('/api/history'); toast('History cleared.', 'success'); load(); }
    catch (e) { toast(e.message, 'error'); }
  });

  document.addEventListener('DOMContentLoaded', load);
})();
