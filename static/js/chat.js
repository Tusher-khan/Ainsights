/* AInsight — AI Chat workspace. */

(() => {
  'use strict';

  const { api, number, integer, duration, escapeHtml, toast, renderTable } = AInsight;

  const scroll = document.getElementById('chat-scroll');
  const inner = document.getElementById('chat-inner');
  const form = document.getElementById('composer');
  const input = document.getElementById('question');
  const sendButton = document.getElementById('send');
  const datasetSelect = document.getElementById('dataset-select');
  const datasetStatus = document.getElementById('dataset-status');
  const welcome = document.getElementById('welcome');
  const followupsBox = document.getElementById('followups');

  let conversationId = null;
  let busy = false;

  /* -------------------------------------------------------- progress UI */

  const PIPELINE = [
    ['classify_intent', 'Understanding your question'],
    ['retrieve_metadata', 'Finding the relevant data'],
    ['plan_query', 'Planning the analysis'],
    ['execute_query', 'Running the analysis'],
    ['validate_result', 'Verifying the result'],
    ['generate_response', 'Preparing the insight'],
  ];

  function showProgress() {
    const node = document.createElement('div');
    node.className = 'progress-card';
    node.innerHTML = `
      <div class="title">AInsight is analysing your data…</div>
      <ul class="stage-list">
        ${PIPELINE.map(([key, label], i) => `
          <li class="stage ${i === 0 ? 'active' : ''}" data-stage="${key}">
            <span class="marker" aria-hidden="true">✓</span>
            <span class="text">${label}</span>
          </li>`).join('')}
      </ul>`;
    inner.appendChild(node);
    scrollToEnd();

    // Advance the indicator on a timer; real stage timings replace it on arrival.
    let index = 0;
    const timer = setInterval(() => {
      const stages = node.querySelectorAll('.stage');
      if (index < stages.length - 1) {
        stages[index].classList.remove('active');
        stages[index].classList.add('done');
        index += 1;
        stages[index].classList.add('active');
      }
    }, 320);

    return { node, stop: () => clearInterval(timer) };
  }

  /* ------------------------------------------------------------ records */

  function verificationStrip(payload) {
    const validation = payload.validation || {};
    const groups = ['plan', 'query', 'result'];
    const checks = [];
    groups.forEach((group) => {
      const report = validation[group];
      if (!report || !report.checks) return;
      report.checks.forEach((check) => checks.push(check));
    });
    const warnings = groups.flatMap((g) => (validation[g] && validation[g].warnings) || []);

    if (!checks.length && !warnings.length) return '';
    const items = checks.map((check) => `
      <span class="check ${check.passed ? '' : 'failed'}">
        <span aria-hidden="true">${check.passed ? '✓' : '✕'}</span>
        ${escapeHtml(humanCheck(check.name))}
      </span>`).join('');
    const warns = warnings.map((w) => `<span class="check warn"><span aria-hidden="true">!</span> ${escapeHtml(w)}</span>`).join('');
    return `
      <div class="record-section">
        <h4>Validation</h4>
        <div class="verification">${items}${warns}</div>
      </div>`;
  }

  function humanCheck(name) {
    const map = {
      columns_exist: 'Columns exist in the dataset',
      measures_numeric: 'Measure is numeric',
      plan_shape: 'Analysis plan verified',
      sql_security: 'Query is read-only',
      schema_awareness: 'Query matches the schema',
      row_limit: 'Row limit applied',
      operation_whitelisted: 'Operation is on the allow-list',
      no_dynamic_code: 'No dynamic code executed',
      execution_success: 'Query executed',
      expected_columns: 'Expected columns returned',
      value_integrity: 'Values checked for gaps',
      aggregation_sanity: 'Aggregation sanity checked',
      cardinality_plausible: 'Row count plausible',
      visualization_compatible: 'Chart matches the result',
      result_shape: 'Result shape verified',
      non_empty_result: 'Query matched no rows',
      metadata_only: 'Answered from the dataset profile',
      time_column_exists: 'Date column exists',
    };
    if (map[name]) return map[name];
    if (name.startsWith('filter_column:')) return `Filter column ${name.split(':')[1]} exists`;
    return name.replace(/_/g, ' ');
  }

  function highlightSql(sql) {
    return escapeHtml(sql)
      .replace(/'([^']*)'/g, '<span class="sql-str">\'$1\'</span>')
      .replace(/\b(SELECT|FROM|WHERE|GROUP BY|ORDER BY|HAVING|LIMIT|JOIN|LEFT|INNER|ON|AS|AND|OR|NOT|IN|BETWEEN|DESC|ASC|WITH|CASE|WHEN|THEN|ELSE|END|DISTINCT)\b/gi,
        '<span class="sql-kw">$1</span>')
      .replace(/\b(SUM|AVG|COUNT|MIN|MAX|ROUND|STRFTIME|CAST|COALESCE)\(/gi, '<span class="sql-fn">$1</span>(')
      .replace(/\b(\d+\.?\d*)\b/g, '<span class="sql-num">$1</span>');
  }

  function renderRecord(payload) {
    const record = document.createElement('div');
    const empty = payload.empty || (payload.result && payload.result.row_count === 0);
    record.className = `record${empty ? ' is-empty' : ''}`;

    const head = payload.headline;
    const source = payload.source || {};
    const result = payload.result || {};
    const sections = [];

    if (head) {
      const exact = typeof head.raw_value === 'number' && Math.abs(head.raw_value) >= 1000
        ? `<span class="exact">exact: ${head.raw_value.toLocaleString(undefined, { maximumFractionDigits: 2 })}</span>` : '';
      sections.push(`
        <div class="record-section">
          <h4>${escapeHtml(head.label)}</h4>
          <div class="record-headline">
            <span class="value">${escapeHtml(head.value)}</span>
            ${exact}
          </div>
        </div>`);
    } else if (payload.answer) {
      sections.push(`<div class="record-section"><div class="record-headline"><span class="label">${escapeHtml(payload.answer)}</span></div></div>`);
    }

    if (payload.insight) {
      sections.push(`<div class="record-section"><h4>Insight</h4><div class="record-insight">${escapeHtml(payload.insight)}</div></div>`);
    }

    (payload.assumptions || []).forEach((assumption) => {
      sections.push(`<div class="record-section"><div class="assumption">${escapeHtml(assumption)}</div></div>`);
    });

    const chartId = `chart-${Math.random().toString(36).slice(2, 9)}`;
    if (payload.visualization && payload.visualization.figure) {
      sections.push(`<div class="record-section"><h4>Visualization</h4><div class="record-chart" id="${chartId}"></div></div>`);
    }

    if (payload.columns_table) {
      sections.push(`<div class="record-section"><h4>Columns</h4>${renderTable(payload.columns_table.columns, payload.columns_table.rows, 60)}</div>`);
    } else if (result.rows && result.rows.length && !(head && result.row_count === 1)) {
      sections.push(`<div class="record-section"><h4>Result data</h4>${renderTable(result.columns, result.rows, 50)}</div>`);
    }

    if (source.dataset) {
      const columns = (source.columns_used || []).length ? source.columns_used.join(', ') : '—';
      sections.push(`
        <div class="record-section">
          <h4>Analysis source</h4>
          <div class="source-grid">
            <div class="source-item"><span class="k">Dataset</span><span class="v">${escapeHtml(source.dataset)}${source.sheet ? ' · ' + escapeHtml(source.sheet) : ''}</span></div>
            <div class="source-item"><span class="k">Columns used</span><span class="v">${escapeHtml(columns)}</span></div>
            <div class="source-item"><span class="k">Operation</span><span class="v mono">${escapeHtml(source.operation || '—')}</span></div>
            <div class="source-item"><span class="k">Rows returned</span><span class="v">${integer(result.row_count || 0)}</span></div>
            <div class="source-item"><span class="k">Execution time</span><span class="v">${duration(result.execution_ms)}</span></div>
            <div class="source-item"><span class="k">Engine</span><span class="v">${escapeHtml((result.engine || 'pandas') === 'sql' ? 'SQL (read-only)' : 'Pandas analytics')}</span></div>
          </div>
        </div>`);
    }

    sections.push(verificationStrip(payload));

    if (payload.sql) {
      sections.push(`
        <details class="sql-panel">
          <summary>View SQL</summary>
          <div class="sql-body">
            <button class="btn btn-sm copy" data-copy="${escapeHtml(payload.sql)}">Copy</button>
            <pre><code>${highlightSql(payload.sql)}</code></pre>
          </div>
        </details>`);
    }

    const historyId = payload.history_id || '';
    sections.push(`
      <div class="record-foot">
        <span class="small muted">${duration(payload.total_ms)} total${payload.routing_reason ? ' · ' + escapeHtml(payload.routing_reason) : ''}</span>
        <span class="spacer"></span>
        ${historyId && result.rows && result.rows.length ? `
          <a class="btn btn-sm" href="/api/download/${historyId}.csv">CSV</a>
          <a class="btn btn-sm" href="/api/download/${historyId}.xlsx">Excel</a>` : ''}
        <div class="feedback" data-history="${historyId}">
          <button class="btn btn-sm btn-ghost" data-rating="helpful" aria-label="Mark as helpful">👍</button>
          <button class="btn btn-sm btn-ghost" data-rating="not_helpful" aria-label="Mark as not helpful">👎</button>
        </div>
      </div>`);

    record.innerHTML = sections.join('');
    inner.appendChild(record);

    if (payload.visualization && payload.visualization.figure) {
      AInsightCharts.render(chartId, payload.visualization.figure);
    }
    wireRecord(record);
    renderFollowups(payload.suggestions || []);
    scrollToEnd();
  }

  function wireRecord(record) {
    record.querySelectorAll('[data-copy]').forEach((button) => {
      button.addEventListener('click', async () => {
        try {
          await navigator.clipboard.writeText(button.dataset.copy);
          button.textContent = 'Copied';
          setTimeout(() => { button.textContent = 'Copy'; }, 1500);
        } catch (e) { toast('Copying is blocked in this browser.', 'error'); }
      });
    });

    const feedback = record.querySelector('.feedback');
    if (feedback) {
      feedback.querySelectorAll('[data-rating]').forEach((button) => {
        button.addEventListener('click', async () => {
          try {
            await api.post('/api/feedback', {
              history_id: feedback.dataset.history || null,
              rating: button.dataset.rating,
            });
            feedback.innerHTML = '<span class="feedback-done">Thanks — feedback recorded</span>';
          } catch (e) { toast(e.message, 'error'); }
        });
      });
    }
  }

  function renderQuestion(text) {
    if (welcome) welcome.style.display = 'none';
    const node = document.createElement('div');
    node.className = 'turn-question';
    node.innerHTML = `<div class="text">${escapeHtml(text)}</div>`;
    inner.appendChild(node);
    scrollToEnd();
  }

  function renderError(message) {
    const node = document.createElement('div');
    node.className = 'record is-error';
    node.innerHTML = `
      <div class="record-section">
        <h4>AInsight could not run that analysis</h4>
        <div class="record-insight">${escapeHtml(message)}</div>
      </div>`;
    inner.appendChild(node);
    scrollToEnd();
  }

  function renderFollowups(suggestions) {
    if (!followupsBox) return;
    followupsBox.innerHTML = suggestions.slice(0, 4)
      .map((s) => `<button type="button" class="followup">${escapeHtml(s)}</button>`).join('');
    followupsBox.querySelectorAll('.followup').forEach((button) => {
      button.addEventListener('click', () => { input.value = button.textContent; input.focus(); });
    });
  }

  function scrollToEnd() {
    requestAnimationFrame(() => { scroll.scrollTop = scroll.scrollHeight; });
  }

  /* --------------------------------------------------------------- flow */

  async function ask(question) {
    if (busy || !question.trim()) return;
    busy = true;
    sendButton.disabled = true;
    renderQuestion(question);
    input.value = '';
    input.style.height = 'auto';
    if (followupsBox) followupsBox.innerHTML = '';

    const progress = showProgress();
    try {
      const payload = await api.post('/api/chat', {
        question,
        dataset_id: datasetSelect ? datasetSelect.value || null : null,
        conversation_id: conversationId,
      });
      conversationId = payload.conversation_id || conversationId;
      progress.stop();
      progress.node.remove();
      renderRecord(payload);
    } catch (error) {
      progress.stop();
      progress.node.remove();
      renderError(error.message);
    } finally {
      busy = false;
      sendButton.disabled = false;
      input.focus();
    }
  }

  async function loadSuggestions() {
    const box = document.getElementById('suggestions');
    if (!box) return;
    const datasetId = datasetSelect ? datasetSelect.value : '';
    try {
      const data = await api.get(`/api/chat/suggestions${datasetId ? `?dataset_id=${datasetId}` : ''}`);
      box.innerHTML = (data.suggestions || []).map((s) => `
        <button class="suggestion" type="button" role="listitem">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6"/></svg>
          ${escapeHtml(s)}
        </button>`).join('');
      box.querySelectorAll('.suggestion').forEach((button) => {
        button.addEventListener('click', () => ask(button.textContent.trim()));
      });
    } catch (e) { /* suggestions are optional */ }
  }

  async function init() {
    const datasets = await AInsight.loadDatasetOptions('dataset-select', { includeAll: true });
    if (datasetStatus) {
      if (datasets.length) {
        datasetStatus.className = 'tag tag-ready';
        datasetStatus.innerHTML = `<span class="dot"></span> ${datasets.length} dataset${datasets.length > 1 ? 's' : ''} ready`;
      } else {
        datasetStatus.className = 'tag tag-warn';
        datasetStatus.innerHTML = '<span class="dot"></span> No data yet';
      }
    }
    await loadSuggestions();
    if (datasetSelect) datasetSelect.addEventListener('change', loadSuggestions);
  }

  form.addEventListener('submit', (event) => {
    event.preventDefault();
    ask(input.value);
  });

  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      ask(input.value);
    }
  });

  input.addEventListener('input', () => {
    input.style.height = 'auto';
    input.style.height = `${Math.min(input.scrollHeight, 160)}px`;
  });

  const clearButton = document.getElementById('clear-chat');
  if (clearButton) {
    clearButton.addEventListener('click', async () => {
      if (conversationId) {
        try { await api.del(`/api/conversations/${conversationId}`); } catch (e) {}
      }
      conversationId = null;
      inner.innerHTML = '';
      if (welcome) { inner.appendChild(welcome); welcome.style.display = ''; }
      loadSuggestions();
      toast('Conversation cleared.', 'success');
    });
  }

  const newChat = document.getElementById('new-chat');
  if (newChat) newChat.addEventListener('click', () => clearButton && clearButton.click());

  // Deep link: /chat?q=...&dataset=...
  init().then(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get('dataset') && datasetSelect) datasetSelect.value = params.get('dataset');
    if (params.get('q')) ask(params.get('q'));
    input.focus();
  });
})();
