'use strict';
let apiKey = '';
let selectedRunId = null;
let stream = null;
const el = id => document.getElementById(id);
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const scoreText = value => value == null ? '—' : `${(value*100).toFixed(1)}%`;

async function apiFetch(path) {
  const response = await fetch(path, {headers: apiKey ? {'X-API-Key': apiKey} : {}});
  if (!response.ok) throw new Error(response.status === 401 ? 'Enter a valid API key.' : `HTTP ${response.status}`);
  return response.json();
}

async function checkHealth() {
  try {
    const response = await fetch('/ready');
    el('api-status').textContent = response.ok ? '● Stack ready' : '● Dependencies unavailable';
    el('api-status').className = response.ok ? 'api-ok' : 'api-err';
  } catch (_) { el('api-status').textContent = '● API unreachable'; }
}

async function loadRuns() {
  try {
    const runs = await apiFetch('/results/');
    el('access-message').textContent = 'Connected. API key stays in this tab only.';
    const tbody = el('run-tbody');
    tbody.innerHTML = '';
    if (!runs.length) tbody.innerHTML = '<tr><td colspan="5">No evaluations yet. Submit one through POST /evaluate/.</td></tr>';
    for (const run of runs) {
      const tr = document.createElement('tr');
      tr.dataset.runId = run.run_id;
      if (run.run_id === selectedRunId) tr.classList.add('selected');
      const status = ['pending','running','complete','failed'].includes(run.status) ? run.status : 'pending';
      tr.innerHTML = `<td><span class="sha">${escapeHtml(run.commit_sha.slice(0,10))}</span></td><td>${escapeHtml(run.branch)}</td>
        <td><span class="badge badge-${status}">${status}</span></td><td>${run.passed === true ? 'Passed' : run.passed === false ? 'Not passed' : 'Pending'}</td>
        <td>${escapeHtml(new Date(run.created_at).toLocaleString())}</td>`;
      tr.addEventListener('click', () => selectRun(run.run_id));
      tbody.appendChild(tr);
    }
    if (!selectedRunId && runs.length) {
      const requested = new URLSearchParams(location.search).get('run_id');
      selectRun(runs.find(r => r.run_id === requested)?.run_id || runs[0].run_id);
    }
  } catch (error) { el('access-message').textContent = error.message; }
}

function renderProgress(data) {
  if (data.run_id !== selectedRunId) return;
  if (data.evaluation_mode === 'fixture') el('evaluation-mode').textContent = 'Deterministic integration fixture — no live quality validation.';
  el('evaluation-progress').max = data.total;
  el('evaluation-progress').value = data.completed;
  el('progress-label').textContent = `${data.stage.replace(/_/g, ' ')}: ${data.completed}/${data.total} — ${data.status}`;
  el('progress-counts').textContent = `Attempted ${data.attempted} · Scored ${data.successfully_scored} · Failed ${data.failed}`;
  el('progress-error').textContent = data.error || '';
}

function selectRun(runId) {
  stream?.disconnect();
  selectedRunId = runId;
  document.querySelectorAll('#run-tbody tr').forEach(row => row.classList.toggle('selected', row.dataset.runId === runId));
  el('progress-error').textContent = '';
  el('evaluation-mode').textContent = '';
  el('progress-label').textContent = 'Connecting to selected run…';
  el('progress-counts').textContent = '';
  el('evaluation-progress').removeAttribute('value');
  el('fp-content').textContent = 'Loading selected run…';
  el('heatmap-content').textContent = 'Scores will appear after this run completes.';
  el('heatmap-run-label').textContent = runId.slice(0, 8);
  el('clusters-list').textContent = 'Waiting for this run’s completed report.';
  el('clusters-count').textContent = '';
  const terminal = data => { renderProgress(data); loadDetails(runId); loadRuns(); };
  stream = new SSEClient(runId, {onProgress: renderProgress, onDone: terminal, onError: terminal,
    onAuthError: message => { el('access-message').textContent = message; },
    onConnectionChange: state => { el('stream-state').textContent = `· ${state}`; },
    onConnectionError: () => { el('stream-state').textContent = '· reconnecting'; }
  }, '/stream', () => apiKey);
  stream.connect();
  loadDetails(runId);
}

async function loadDetails(runId) {
  try {
    const detail = await apiFetch(`/results/${runId}`);
    if (runId !== selectedRunId) return;
    const config = detail.provenance?.configuration;
    el('evaluation-mode').textContent = config?.scoring_mode === 'fixture' ? 'Deterministic integration fixture — no live quality validation.' : 'Ragas evaluation; attribution rules are not calibrated probabilities.';
    el('progress-error').textContent = detail.error_message || '';
    renderHeatmap(detail.category_scores || {});
    if (detail.fingerprint_available && detail.status === 'complete') {
      const report = await apiFetch(`/results/${runId}/fingerprint`);
      if (runId === selectedRunId) renderFingerprint(report);
    } else {
      el('fp-content').textContent = detail.status === 'failed' ? 'Evaluation failed; no valid regression comparison.' : 'Evaluation is in progress.';
      el('clusters-list').textContent = 'No completed regression report.';
    }
  } catch (error) {
    if (runId === selectedRunId) el('fp-content').textContent = error.message === 'HTTP 425' ? 'Evaluation is queued.' : error.message;
  }
}

function renderFingerprint(report) {
  const bootstrap = report.comparison_status === 'bootstrap';
  el('fp-content').innerHTML = `<p>${bootstrap ? 'Baseline bootstrap — no regression comparison.' : escapeHtml(report.summary)}</p>
    <p>Absolute quality: ${report.quality_passed ? 'Passed' : 'Failed'}</p>
    <p>Investigation: ${escapeHtml(report.top_failing_component || 'No failure attribution')}</p>
    <p style="font-size:.75rem;color:var(--text-dim)">Heuristic evidence; not causal certainty or calibrated probabilities.</p>`;
  el('clusters-count').textContent = `${report.regressions.length} metric regressions`;
  el('clusters-list').innerHTML = report.regressions.length ? report.regressions.map(r => `<article class="cluster-card">
    <p><strong>${escapeHtml(r.category)}</strong> · ${escapeHtml(r.severity)}</p>
    <p>${escapeHtml(r.metric)}: ${r.delta.toFixed(3)}</p><p>${escapeHtml(r.attributed_to)}</p>
    <p>${escapeHtml(r.suggested_fix)}</p></article>`).join('') : 'No metric regressions recorded.';
}

function renderHeatmap(categories) {
  const metrics = ['correctness','grounding','context_recall','context_precision'];
  const rows = Object.entries(categories).map(([category, row]) => `<tr><td>${escapeHtml(category.replace(/_/g,' '))}</td>${metrics.map(m => `<td>${scoreText(row[`avg_${m}`])}</td>`).join('')}</tr>`).join('');
  el('heatmap-content').innerHTML = rows ? `<table id="heatmap-table"><thead><tr><th>Category</th>${metrics.map(m => `<th>${m.replace(/_/g,' ')}</th>`).join('')}</tr></thead><tbody>${rows}</tbody></table>` : 'Scores will appear after evaluation completes.';
}

document.addEventListener('DOMContentLoaded', () => {
  el('api-key-form').addEventListener('submit', event => {
    event.preventDefault();
    apiKey = el('api-key').value.trim();
    stream?.disconnect();
    selectedRunId = null;
    loadRuns();
  });
  checkHealth();
  loadRuns();
  setInterval(loadRuns, 10000);
});
window.addEventListener('beforeunload', () => stream?.disconnect());
window.loadRuns = loadRuns;
