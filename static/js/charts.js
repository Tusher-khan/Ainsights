/* AInsight — Plotly rendering with theme awareness.
   Figures always arrive fully specified from the server; this module only
   applies theme colours and responsive behaviour. It never invents data. */

const AInsightCharts = (() => {
  'use strict';

  const registry = new Map();

  function palette() {
    const styles = getComputedStyle(document.documentElement);
    return {
      font: styles.getPropertyValue('--ink-2').trim() || '#33415C',
      grid: styles.getPropertyValue('--line').trim() || '#E3E8EF',
      accent: styles.getPropertyValue('--accent').trim() || '#2F5AE0',
    };
  }

  function themed(figure) {
    const colors = palette();
    const layout = JSON.parse(JSON.stringify(figure.layout || {}));
    layout.paper_bgcolor = 'rgba(0,0,0,0)';
    layout.plot_bgcolor = 'rgba(0,0,0,0)';
    layout.font = Object.assign({}, layout.font, { color: colors.font });
    ['xaxis', 'yaxis'].forEach((axis) => {
      if (layout[axis]) {
        layout[axis].gridcolor = colors.grid;
        layout[axis].linecolor = colors.grid;
        layout[axis].zerolinecolor = colors.grid;
      }
    });
    if (layout.title && layout.title.font) layout.title.font.color = colors.font;
    return layout;
  }

  function render(elementId, figure) {
    const element = document.getElementById(elementId);
    if (!element || !figure) return;
    if (typeof Plotly === 'undefined') {
      // The charting library could not load (offline or blocked CDN). Say so
      // rather than leaving an empty panel; the result table still has the data.
      element.innerHTML =
        '<p class="muted small">The chart library could not load, so this result is shown as a table only.</p>';
      return;
    }
    const config = Object.assign({ displayModeBar: false, responsive: true }, figure.config || {});
    Plotly.newPlot(element, figure.data, themed(figure), config);
    registry.set(elementId, figure);
  }

  function retheme() {
    registry.forEach((figure, elementId) => {
      const element = document.getElementById(elementId);
      if (element && typeof Plotly !== 'undefined') Plotly.relayout(element, themed(figure));
    });
  }

  document.addEventListener('ainsight:theme', retheme);
  window.addEventListener('resize', () => {
    registry.forEach((_figure, elementId) => {
      const element = document.getElementById(elementId);
      if (element && typeof Plotly !== 'undefined') Plotly.Plots.resize(element);
    });
  });

  return { render, retheme };
})();
