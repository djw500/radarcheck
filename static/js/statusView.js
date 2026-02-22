// --- Auth ---
const STATUS_API_KEY = new URLSearchParams(window.location.search).get('api_key') || '';

// --- Eager timeout fetch ---
async function fetchJSON(url) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 15000);
    const headers = {};
    if (STATUS_API_KEY) headers['X-API-Key'] = STATUS_API_KEY;
    try {
        const res = await fetch(url, { headers, signal: ctrl.signal });
        if (!res.ok) throw new Error(await res.text());
        return res.json();
    } finally {
        clearTimeout(timer);
    }
}

async function postJSON(url, body) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 5000);
    const headers = { 'Content-Type': 'application/json' };
    if (STATUS_API_KEY) headers['X-API-Key'] = STATUS_API_KEY;
    try {
        const res = await fetch(url, {
            method: 'POST',
            headers,
            body: JSON.stringify(body),
            signal: ctrl.signal,
        });
        if (!res.ok) throw new Error(await res.text());
        return res.json();
    } finally {
        clearTimeout(timer);
    }
}

// --- State ---
let refreshInterval = null;
let countdown = 10;
let jobsFilter = null;

// --- Init ---
async function initStatusPage() {
    await refreshAll();
    startAutoRefresh();
    bindButtons();
}

function bindButtons() {
    const refreshBtn = document.getElementById('refreshBtn');
    if (refreshBtn) refreshBtn.onclick = () => { refreshAll(); resetCountdown(); };

    const refreshLogsBtn = document.getElementById('refreshLogsBtn');
    if (refreshLogsBtn) refreshLogsBtn.onclick = loadLogs;

    const retryFailedBtn = document.getElementById('retryFailedBtn');
    if (retryFailedBtn) retryFailedBtn.onclick = async () => {
        try {
            const result = await postJSON('/api/jobs/retry-failed', {});
            await refreshAll();
        } catch (e) { console.error('Retry failed:', e); }
    };

    const cancelPendingBtn = document.getElementById('cancelPendingBtn');
    if (cancelPendingBtn) cancelPendingBtn.onclick = async () => {
        try {
            const result = await postJSON('/api/jobs/cancel', { status: 'pending' });
            await refreshAll();
        } catch (e) { console.error('Cancel failed:', e); }
    };

    const showFailedBtn = document.getElementById('showFailedBtn');
    if (showFailedBtn) showFailedBtn.onclick = () => { jobsFilter = 'failed'; loadJobs(); };

    const showAllBtn = document.getElementById('showAllBtn');
    if (showAllBtn) showAllBtn.onclick = () => { jobsFilter = null; loadJobs(); };
}

// --- Auto-refresh ---
function startAutoRefresh() {
    countdown = 10;
    updateCountdownDisplay();
    if (refreshInterval) clearInterval(refreshInterval);
    refreshInterval = setInterval(async () => {
        countdown--;
        if (countdown <= 0) { await refreshAll(); countdown = 10; }
        updateCountdownDisplay();
    }, 1000);
}

function resetCountdown() { countdown = 10; updateCountdownDisplay(); }

function updateCountdownDisplay() {
    const el = document.getElementById('refreshCountdown');
    if (el) el.textContent = `${countdown}s`;
}

// --- Data loading ---
async function refreshAll() {
    try {
        const [summaryData, gridData] = await Promise.all([
            fetchJSON('/api/status/summary'),
            fetchJSON('/api/status/run-grid'),
        ]);
        renderJobQueue(summaryData.job_queue);
        renderSchedulerStats(summaryData.scheduler_status);
        renderDiskUsage(summaryData.disk_usage);
        renderRunGrid(gridData);
    } catch (e) {
        console.error('Failed to load status:', e);
    }
    loadJobs().catch(e => console.error('Failed to load jobs:', e));
    loadLogs().catch(e => console.error('Failed to load logs:', e));
}

async function loadJobs() {
    try {
        const params = new URLSearchParams({ limit: '50' });
        if (jobsFilter) params.set('status', jobsFilter);
        const data = await fetchJSON(`/api/jobs/list?${params}`);
        renderJobsTable(data.jobs);
    } catch (e) { console.error('Failed to load jobs:', e); }
}

async function loadLogs() {
    try {
        const logs = await fetchJSON('/api/status/logs?lines=50');
        renderLogs(logs.lines);
    } catch (e) { console.error('Failed to load logs:', e); }
}

// --- Formatters ---
function formatBytes(bytes) {
    if (!+bytes) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
}

function formatTimeShort(isoStr) {
    if (!isoStr) return '-';
    const d = new Date(isoStr);
    return d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false });
}

// --- Top-level renderers ---
function renderJobQueue(q) {
    const s = (id, v) => { const e = document.getElementById(id); if (e) e.textContent = v ?? 0; };
    s('qPending', q.pending); s('qProcessing', q.processing);
    s('qFailed', q.failed); s('qCompleted', q.completed);
}

function renderSchedulerStats(status) {
    if (!status) return;
    const e = id => document.getElementById(id);
    if (e('schedState')) e('schedState').textContent = status.state || '-';
    if (e('schedLastRun') && status.last_run) e('schedLastRun').textContent = formatTimeShort(status.last_run);
    if (e('schedNextRun') && status.next_run) e('schedNextRun').textContent = formatTimeShort(status.next_run);
    if (e('schedTargets')) e('schedTargets').textContent = (status.targets?.length > 0) ? status.targets.join(', ') : 'None';
}

function renderDiskUsage(u) {
    if (!u) return;
    const e = id => document.getElementById(id);
    if (e('diskTotal')) e('diskTotal').textContent = formatBytes(u.total);
    if (e('diskGribs')) e('diskGribs').textContent = formatBytes(u.gribs?.total || 0);
    if (e('diskTiles')) e('diskTiles').textContent = formatBytes(u.tiles?.total || 0);
}

// --- Run Grid: table summary view ---

function renderRunGrid(gridData) {
    const container = document.getElementById('statusGrid');
    if (!container) return;

    if (!gridData || Object.keys(gridData).length === 0) {
        container.innerHTML = '<p class="text-slate-500">No job data found.</p>';
        return;
    }

    const modelOrder = ['hrrr', 'nam_nest', 'gfs', 'nbm', 'ecmwf_hres'];
    let html = '';
    const rendered = new Set();
    for (const id of modelOrder) {
        if (gridData[id]) { html += buildModelTable(id, gridData[id]); rendered.add(id); }
    }
    for (const id of Object.keys(gridData)) {
        if (!rendered.has(id)) html += buildModelTable(id, gridData[id]);
    }

    container.innerHTML = html;
    bindGridActions(container);
}

function varCell(s) {
    // s = {completed, pending, failed, processing, total}
    if (!s || s.total === 0) return `<td class="px-2 py-1 text-center text-slate-400">-</td>`;

    const done = s.completed;
    const total = s.total;

    // Pick color based on aggregate status
    let cls, text;
    if (done === total) {
        cls = 'text-emerald-600 dark:text-emerald-400';
        text = `${done}`;
    } else if (s.processing > 0) {
        cls = 'text-blue-600 dark:text-blue-400 font-medium';
        text = `${done}/${total}`;
    } else if (s.pending > 0) {
        cls = 'text-amber-600 dark:text-amber-400';
        text = `${done}/${total}`;
    } else if (s.failed > 0 && done === 0) {
        cls = 'text-red-500 dark:text-red-400';
        text = `FAIL`;
    } else if (s.failed > 0) {
        cls = 'text-red-500 dark:text-red-400';
        text = `${done}/${total}`;
    } else {
        cls = 'text-slate-500';
        text = `${done}/${total}`;
    }

    return `<td class="px-2 py-1 text-center text-xs font-mono ${cls}" title="${done} done, ${s.pending} pending, ${s.processing} active, ${s.failed} failed">${text}</td>`;
}

function pctCell(totals) {
    if (!totals || totals.total === 0) return `<td class="px-2 py-1 text-center text-slate-400">-</td>`;
    const pct = Math.round((totals.completed / totals.total) * 100);
    let cls;
    if (pct === 100) cls = 'text-emerald-600 dark:text-emerald-400 font-bold';
    else if (pct > 0) cls = 'text-amber-600 dark:text-amber-400 font-semibold';
    else cls = 'text-red-500 dark:text-red-400 font-semibold';

    // Compact inline bar
    const bar = pct > 0
        ? `<div class="w-full bg-slate-200 dark:bg-slate-700 rounded-full h-1.5 mt-0.5"><div class="h-1.5 rounded-full ${pct === 100 ? 'bg-emerald-500' : pct > 50 ? 'bg-amber-400' : 'bg-red-400'}" style="width:${pct}%"></div></div>`
        : '';
    return `<td class="px-2 py-1 text-center text-xs ${cls}" title="${totals.completed}/${totals.total} jobs">${pct}%${bar}</td>`;
}

function buildModelTable(modelId, model) {
    const vars = model.variables || [];
    const runs = model.runs || [];
    const available = model.available_runs || [];

    let h = `<div class="mb-6">`;

    // Header
    h += `<div class="flex justify-between items-center mb-2">`;
    h += `<div class="text-sm font-semibold text-slate-900 dark:text-white">${esc(model.name)}</div>`;
    h += `<div class="flex gap-2 items-center">`;
    h += `<select data-backfill-model="${modelId}" class="text-xs rounded border border-slate-300 dark:border-slate-600 dark:bg-slate-700 dark:text-slate-200 px-2 py-1">`;
    h += `<option value="">Enqueue run...</option>`;
    for (const rid of available) {
        const p = rid.split('_');
        const lbl = `${p[1].slice(4,6)}/${p[1].slice(6,8)} ${p[2]}Z`;
        const exists = runs.some(r => r.run_id === rid);
        h += `<option value="${rid}">${lbl}${exists ? '' : ' (new)'}</option>`;
    }
    h += `</select>`;
    h += `<button data-backfill-trigger="${modelId}" class="px-2 py-1 rounded text-xs font-medium bg-primary/10 text-primary hover:bg-primary/20 transition-colors">Enqueue</button>`;
    h += `</div></div>`;

    if (runs.length === 0 && vars.length === 0) {
        h += `<p class="text-xs text-slate-400">No jobs for this model.</p></div>`;
        return h;
    }

    // Table
    h += `<div class="overflow-x-auto"><table class="min-w-full text-xs text-left">`;
    h += `<thead class="text-[10px] uppercase bg-slate-50 dark:bg-slate-700 text-slate-500 dark:text-slate-300"><tr>`;
    h += `<th class="px-2 py-2 sticky left-0 bg-slate-50 dark:bg-slate-700 z-10">Run</th>`;
    for (const v of vars) {
        const short = v.length > 6 ? v.slice(0, 5) + '..' : v;
        h += `<th class="px-2 py-2 text-center" title="${v}">${short}</th>`;
    }
    h += `<th class="px-2 py-2 text-center w-20">Total</th>`;
    h += `</tr></thead><tbody>`;

    for (const run of runs) {
        h += `<tr class="bg-white dark:bg-slate-800 border-b dark:border-slate-700 hover:bg-slate-50 dark:hover:bg-slate-750">`;
        h += `<td class="px-2 py-1.5 font-mono text-[11px] text-slate-700 dark:text-slate-300 sticky left-0 bg-white dark:bg-slate-800 z-10 whitespace-nowrap">${esc(run.display)}</td>`;
        for (const v of vars) {
            h += varCell(run.variables[v]);
        }
        h += pctCell(run.totals);
        h += `</tr>`;
    }

    h += `</tbody></table></div></div>`;
    return h;
}

function bindGridActions(container) {
    container.querySelectorAll('[data-backfill-trigger]').forEach(btn => {
        btn.onclick = async () => {
            const modelId = btn.dataset.backfillTrigger;
            const select = container.querySelector(`[data-backfill-model="${modelId}"]`);
            const runId = select?.value;
            if (!runId) return;

            btn.disabled = true; btn.textContent = '...';
            try {
                const result = await postJSON('/api/jobs/enqueue-run', {
                    model_id: modelId, run_id: runId, region_id: 'ne'
                });
                btn.textContent = `+${result.enqueued}`;
                setTimeout(() => { btn.textContent = 'Enqueue'; btn.disabled = false; refreshAll(); }, 1000);
            } catch (e) {
                console.error('Enqueue failed:', e);
                btn.textContent = 'Error';
                setTimeout(() => { btn.textContent = 'Enqueue'; btn.disabled = false; }, 2000);
            }
        };
    });
}

// --- Jobs table ---
function renderJobsTable(jobs) {
    const tbody = document.getElementById('jobsTableBody');
    if (!tbody) return;

    if (!jobs || jobs.length === 0) {
        tbody.innerHTML = '<tr><td colspan="9" class="px-3 py-4 text-center text-slate-400">No jobs found.</td></tr>';
        return;
    }

    const statusCls = {
        pending: 'bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300',
        processing: 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300',
        completed: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-300',
        failed: 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-300',
    };

    tbody.innerHTML = jobs.map(job => {
        let args = {};
        try { args = JSON.parse(job.args_json); } catch (e) {}
        const sc = statusCls[job.status] || 'bg-slate-100 text-slate-700';
        const t = formatTimeShort(job.completed_at || job.started_at || job.created_at);
        const err = job.error_message
            ? `<span class="text-red-400 truncate max-w-[200px] inline-block" title="${esc(job.error_message)}">${esc(job.error_message.slice(0, 60))}</span>`
            : '';
        return `<tr class="bg-white dark:bg-slate-800 border-b dark:border-slate-700">
            <td class="px-3 py-1.5 font-mono">${job.id}</td>
            <td class="px-3 py-1.5">${args.model_id || '-'}</td>
            <td class="px-3 py-1.5 font-mono">${args.run_id ? args.run_id.split('_').slice(1).join(' ') : '-'}</td>
            <td class="px-3 py-1.5">${args.variable_id || '-'}</td>
            <td class="px-3 py-1.5">H${args.forecast_hour ?? '-'}</td>
            <td class="px-3 py-1.5"><span class="px-2 py-0.5 rounded text-[10px] font-semibold ${sc}">${job.status}</span></td>
            <td class="px-3 py-1.5 font-mono">${t}</td>
            <td class="px-3 py-1.5">${err}</td>
        </tr>`;
    }).join('');
}

// --- Logs ---
function renderLogs(lines) {
    const container = document.getElementById('logsContainer');
    if (!container) return;
    container.innerHTML = lines.map(line => {
        let cls = '';
        if (line.includes('[ERROR]')) cls = 'text-red-400';
        if (line.includes('[WARNING]')) cls = 'text-amber-400';
        return `<div class="${cls}">${esc(line)}</div>`;
    }).join('');
    container.scrollTop = container.scrollHeight;
}

function esc(str) {
    const d = document.createElement('div');
    d.textContent = str;
    return d.innerHTML;
}

if (typeof module !== 'undefined') {
    module.exports = { initStatusPage, renderRunGrid, renderDiskUsage, renderLogs, renderSchedulerStats };
}
