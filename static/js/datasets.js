/* AInsight — dataset cards (used on both the datasets page and the dashboard). */

(() => {
  'use strict';
  const { api, integer, when, escapeHtml, toast } = AInsight;

  function card(dataset) {
    const summary = dataset.summary || {};
    const columns = (summary.measures || []).slice(0, 3).concat((summary.dimensions || []).slice(0, 3));
    return `
      <article class="card dataset-card" data-id="${dataset.id}">
        <div class="body">
          <div class="title">
            <h3>${escapeHtml(dataset.name)}</h3>
            <span class="tag tag-ready" style="margin-left:auto"><span class="dot"></span>${escapeHtml(dataset.status || 'ready')}</span>
          </div>
          <div class="kind">${dataset.source_type === 'excel' ? 'Excel workbook' : dataset.source_type === 'sqlite' ? 'Database table' : 'CSV dataset'}${dataset.sheet_name ? ' · ' + escapeHtml(dataset.sheet_name) : ''}</div>
          <div class="figures">
            <div class="f"><span class="n">${integer(dataset.row_count)}</span><span class="k">rows</span></div>
            <div class="f"><span class="n">${integer(dataset.column_count)}</span><span class="k">columns</span></div>
            ${summary.quality_score !== undefined && summary.quality_score !== null
              ? `<div class="f"><span class="n">${summary.quality_score}</span><span class="k">quality</span></div>` : ''}
          </div>
          <div class="cols">${columns.map((c) => `<span class="tag">${escapeHtml(c)}</span>`).join('')}</div>
        </div>
        <div class="actions">
          <a class="btn btn-sm btn-primary" href="/chat?dataset=${dataset.id}">Open chat</a>
          <a class="btn btn-sm" href="/datasets/${dataset.id}">Analyse</a>
          <span class="spacer"></span>
          <button class="btn btn-sm btn-ghost" data-action="refresh" title="Re-read the file and re-profile it">Refresh</button>
          <button class="btn btn-sm btn-danger" data-action="delete">Delete</button>
        </div>
        <div class="record-foot small muted" style="border-top:1px solid var(--line)">Updated ${when(dataset.updated_at)}</div>
      </article>`;
  }

  function wire(grid) {
    grid.querySelectorAll('[data-action]').forEach((button) => {
      button.addEventListener('click', async () => {
        const article = button.closest('.dataset-card');
        const id = article.dataset.id;
        const action = button.dataset.action;
        if (action === 'delete') {
          if (!window.confirm('Delete this dataset? Its file stays on disk but AInsight will stop using it.')) return;
          try {
            await api.del(`/api/datasets/${id}`);
            article.remove();
            toast('Dataset deleted.', 'success');
          } catch (e) { toast(e.message, 'error'); }
        } else if (action === 'refresh') {
          button.disabled = true;
          button.textContent = 'Refreshing…';
          try {
            await api.post(`/api/datasets/${id}/refresh`);
            toast('Dataset re-profiled.', 'success');
            load();
          } catch (e) {
            toast(e.message, 'error');
            button.disabled = false;
            button.textContent = 'Refresh';
          }
        }
      });
    });
  }

  async function load() {
    const grid = document.getElementById('dataset-grid');
    if (!grid) return;
    grid.innerHTML = '<div class="card card-pad"><div class="skeleton" style="width:50%"></div></div>';
    try {
      const data = await api.get('/api/datasets');
      const datasets = data.datasets || [];
      if (!datasets.length) {
        grid.innerHTML = `
          <div class="card empty" style="grid-column:1/-1">
            <h3>No datasets yet</h3>
            <p>Upload a CSV or Excel file and AInsight will profile it, index its metadata and make it ready to query.</p>
            <a class="btn btn-primary" href="/upload">Upload your first dataset</a>
          </div>`;
      } else {
        grid.innerHTML = datasets.map(card).join('');
        wire(grid);
      }

      const tablesSection = document.getElementById('tables-section');
      const tablesList = document.getElementById('tables-list');
      if (tablesSection && tablesList && data.tables && data.tables.length) {
        tablesSection.hidden = false;
        tablesList.innerHTML = data.tables.map((t) => `
          <div class="activity-row">
            <span class="tag tag-accent">SQL</span>
            <span class="q">${escapeHtml(t.name)}</span>
            <span class="meta">${integer(t.row_count)} rows · ${integer(t.column_count)} columns</span>
          </div>`).join('');
      }
    } catch (e) {
      grid.innerHTML = `<div class="card card-pad"><p class="muted">Datasets could not be loaded: ${escapeHtml(e.message)}</p></div>`;
    }
  }

  document.addEventListener('DOMContentLoaded', load);
})();
