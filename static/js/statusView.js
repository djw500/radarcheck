// --- Eager timeout fetch ---
async function fetchJSON(url) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 5000);
    try {
        const res = await fetch(url, { signal: ctrl.signal });
        if (!res.ok) throw new Error(await res.text());
        return res.json();
    } finally {
        clearTimeout(timer);
    }
}

async function postJSON(url, body) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 5000);
    try {
        const res = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
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
let jobsFilter = null; // null = all, 'failed' = failed only

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
            console.log('Retried:', result.retried);
            await refreshAll();
        } catch (e) {
            console.error('Retry failed:', e);
        }
    };

    const cancelPendingBtn = document.getElementById('cancelPendingBtn');
    if (cancelPendingBtn) cancelPendingBtn.onclick = async () => {
        try {
            const result = await postJSON('/api/jobs/cancel', { status: 'pending' });
            console.log('Cancelled:', result.cancelled);
            await refreshAll();
        } catch (e) {
            console.error('Cancel failed:', e);
        }
    };

    const showFailedBtn = document.getElementById('showFailedBtn');
    if (showFailedBtn) showFailedBtn.onclick = () => {
        jobsFilter = 'failed';
        loadJobs();
    };

    const showAllBtn = document.getElementById('showAllBtn');
    if (showAllBtn) showAllBtn.onclick = () => {
        jobsFilter = null;
        loadJobs();
    };
}

// --- Auto-refresh ---
function startAutoRefresh() {
    countdown = 10;
    updateCountdownDisplay();
    if (refreshInterval) clearInterval(refreshInterval);
    refreshInterval = setInterval(async () => {
        countdown--;
        if (countdown <= 0) {
            await refreshAll();
            countdown = 10;
        }
        updateCountdownDisplay();
    }, 1000);
}

function resetCountdown() {
    countdown = 10;
    updateCountdownDisplay();
}

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

    // Load jobs and logs in parallel but don't block the main refresh
    loadJobs().catch(e => console.error('Failed to load jobs:', e));
    loadLogs().catch(e => console.error('Failed to load logs:', e));
}

async function loadJobs() {
    try {
        const params = new URLSearchParams({ limit: '50' });
        if (jobsFilter) params.set('status', jobsFilter);
        const data = await fetchJSON(`/api/jobs/list?${params}`);
        renderJobsTable(data.jobs);
    } catch (e) {
        console.error('Failed to load jobs:', e);
    }
}

async function loadLogs() {
    try {
        const logs = await fetchJSON('/api/status/logs?lines=50');
        renderLogs(logs.lines);
    } catch (e) {
        console.error('Failed to load logs:', e);
    }
}

// --- Formatters ---
function formatBytes(bytes, decimals = 2) {
    if (!+bytes) return '0 Bytes';
    const k = 1024;
    const dm = decimals < 0 ? 0 : decimals;
    const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return `${parseFloat((bytes / Math.pow(k, i)).toFixed(dm))} ${sizes[i]}`;
}

function formatTime(isoStr) {
    if (!isoStr) return '-';
    const d = new Date(isoStr);
    return d.toLocaleString('en-US', {
        month: 'short', day: 'numeric',
        hour: '2-digit', minute: '2-digit', hour12: false
    });
}

function formatTimeShort(isoStr) {
    if (!isoStr) return '-';
    const d = new Date(isoStr);
    return d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false });
}

// --- Renderers ---
function renderJobQueue(queue) {
    const set = (id, val) => {
        const el = document.getElementById(id);
        if (el) el.textContent = val ?? 0;
    };
    set('qPending', queue.pending);
    set('qProcessing', queue.processing);
    set('qFailed', queue.failed);
    set('qCompleted', queue.completed);
}

function renderSchedulerStats(status) {
    if (!status) return;
    const el = (id) => document.getElementById(id);
    if (el('schedState')) el('schedState').textContent = status.state || '-';
    if (el('schedLastRun') && status.last_run) el('schedLastRun').textContent = formatTimeShort(status.last_run);
    if (el('schedNextRun') && status.next_run) el('schedNextRun').textContent = formatTimeShort(status.next_run);
    if (el('schedTargets')) {
        el('schedTargets').textContent = (status.targets && status.targets.length > 0)
            ? status.targets.join(', ')
            : 'None';
    }
}

function renderDiskUsage(usage) {
    if (!usage) return;
    const el = (id) => document.getElementById(id);
    if (el('diskTotal')) el('diskTotal').textContent = formatBytes(usage.total);
    if (el('diskGribs')) el('diskGribs').textContent = formatBytes(usage.gribs?.total || 0);
    if (el('diskTiles')) el('diskTiles').textContent = formatBytes(usage.tiles?.total || 0);
}

// --- Run Grid (job-status based) ---

const STATUS_COLORS = {
    completed:  'bg-emerald-500',
    processing: 'bg-blue-500',
    pending:    'bg-amber-400',
    failed:     'bg-red-500',
};

const STATUS_BADGE = {
    completed:  'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-300',
    processing: 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300',
    pending:    'bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300',
    failed:     'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-300',
};

function renderRunGrid(gridData) {
    const container = document.getElementById('statusGrid');
    if (!container) return;

    if (!gridData || Object.keys(gridData).length === 0) {
        container.innerHTML = '<p class="text-slate-500">No job data found.</p>';
        return;
    }

    const modelOrder = ['hrrr', 'nam_nest', 'gfs', 'nbm', 'ecmwf_hres'];
    let html = '';

    // Render models in order
    const rendered = new Set();
    for (const modelId of modelOrder) {
        if (gridData[modelId]) {
            html += buildModelSection(modelId, gridData[modelId]);
            rendered.add(modelId);
        }
    }
    // Any remaining
    for (const modelId of Object.keys(gridData)) {
        if (!rendered.has(modelId)) {
            html += buildModelSection(modelId, gridData[modelId]);
        }
    }

    container.innerHTML = html;
    bindGridActions(container);
}

function buildModelSection(modelId, modelData) {
    const runs = modelData.runs || {};
    const availableRuns = modelData.available_runs || [];
    const runIds = Object.keys(runs).sort().reverse(); // newest first

    let html = `<div class="mb-6">`;

    // Header with model name and backfill dropdown
    html += `<div class="flex justify-between items-center mb-2">`;
    html += `<div class="text-sm font-semibold text-slate-900 dark:text-white">${escapeHtml(modelData.name)}</div>`;
    html += `<div class="flex gap-2 items-center">`;

    // Backfill dropdown
    html += `<select data-backfill-model="${modelId}" class="text-xs rounded border border-slate-300 dark:border-slate-600 dark:bg-slate-700 dark:text-slate-200 px-2 py-1">`;
    html += `<option value="">Enqueue run...</option>`;
    for (const runId of availableRuns) {
        const parts = runId.split('_');
        const label = `${parts[1].slice(4,6)}/${parts[1].slice(6,8)} ${parts[2]}Z`;
        const inGrid = runs[runId] !== undefined;
        const suffix = inGrid ? '' : ' (new)';
        html += `<option value="${runId}">${label}${suffix}</option>`;
    }
    html += `</select>`;
    html += `<button data-backfill-trigger="${modelId}" class="px-2 py-1 rounded text-xs font-medium bg-primary/10 text-primary hover:bg-primary/20 transition-colors">Enqueue</button>`;
    html += `</div></div>`;

    if (runIds.length === 0) {
        html += `<p class="text-xs text-slate-400 mb-2">No jobs in queue for this model.</p>`;
        html += `</div>`;
        return html;
    }

    // Run table
    // Collect all hours across runs for column headers
    const allHours = new Set();
    for (const runId of runIds) {
        const hourMap = runs[runId].hours || {};
        for (const h of Object.keys(hourMap)) allHours.add(parseInt(h));
        // Also add expected hours range
        const total = runs[runId].total_hours || 0;
    }
    const sortedHours = Array.from(allHours).sort((a, b) => a - b);

    // Downsample if too many columns
    let displayHours = sortedHours;
    if (sortedHours.length > 60) {
        const step = Math.ceil(sortedHours.length / 60);
        displayHours = sortedHours.filter((_, i) => i % step === 0);
    }

    html += `<div class="overflow-x-auto"><table class="min-w-full text-xs text-left text-slate-500 dark:text-slate-400">`;
    html += `<thead class="text-[10px] uppercase bg-slate-50 dark:bg-slate-700 dark:text-slate-300"><tr>`;
    html += `<th class="px-2 py-2 sticky left-0 bg-slate-50 dark:bg-slate-700 z-10">Run</th>`;
    for (const h of displayHours) {
        html += `<th class="px-0.5 py-2 text-center w-5">${h}</th>`;
    }
    html += `<th class="px-2 py-2 text-center">Status</th>`;
    html += `</tr></thead><tbody>`;

    for (const runId of runIds) {
        const run = runs[runId];
        const hourMap = run.hours || {};
        const counts = run.counts || {};
        const parts = runId.split('_');
        const displayRun = `${parts[1].slice(4,6)}/${parts[1].slice(6,8)} ${parts[2]}Z`;

        html += `<tr class="bg-white dark:bg-slate-800 border-b dark:border-slate-700">`;
        html += `<td class="px-2 py-1.5 font-mono text-[11px] sticky left-0 bg-white dark:bg-slate-800 z-10 whitespace-nowrap">${displayRun}</td>`;

        for (const h of displayHours) {
            const status = hourMap[h];
            if (!status) {
                html += `<td class="px-0 py-1 text-center"><span class="inline-block h-3 w-3 rounded-sm bg-slate-200 dark:bg-slate-700"></span></td>`;
            } else {
                const color = STATUS_COLORS[status] || 'bg-slate-400';
                html += `<td class="px-0 py-1 text-center"><span class="inline-block h-3 w-3 rounded-sm ${color}" title="H${h}: ${status}"></span></td>`;
            }
        }

        // Status summary badges
        html += `<td class="px-2 py-1.5 text-center whitespace-nowrap">`;
        for (const [st, count] of Object.entries(counts)) {
            if (count > 0) {
                const badge = STATUS_BADGE[st] || 'bg-slate-100 text-slate-700';
                html += `<span class="inline-block px-1.5 py-0.5 rounded text-[10px] font-semibold ${badge} mr-1">${count} ${st}</span>`;
            }
        }
        html += `</td>`;
        html += `</tr>`;
    }

    html += `</tbody></table></div></div>`;
    return html;
}

function bindGridActions(container) {
    // Backfill trigger buttons
    container.querySelectorAll('[data-backfill-trigger]').forEach(btn => {
        btn.onclick = async () => {
            const modelId = btn.dataset.backfillTrigger;
            const select = container.querySelector(`[data-backfill-model="${modelId}"]`);
            const runId = select?.value;
            if (!runId) return;

            btn.disabled = true;
            btn.textContent = '...';
            try {
                const result = await postJSON('/api/jobs/enqueue-run', {
                    model_id: modelId, run_id: runId, region_id: 'ne'
                });
                btn.textContent = `+${result.enqueued}`;
                setTimeout(() => {
                    btn.textContent = 'Enqueue';
                    btn.disabled = false;
                    refreshAll();
                }, 1000);
            } catch (e) {
                console.error('Enqueue failed:', e);
                btn.textContent = 'Error';
                setTimeout(() => { btn.textContent = 'Enqueue'; btn.disabled = false; }, 2000);
            }
        };
    });
}

function renderJobsTable(jobs) {
    const tbody = document.getElementById('jobsTableBody');
    if (!tbody) return;

    if (!jobs || jobs.length === 0) {
        tbody.innerHTML = '<tr><td colspan="9" class="px-3 py-4 text-center text-slate-400">No jobs found.</td></tr>';
        return;
    }

    tbody.innerHTML = jobs.map(job => {
        let args = {};
        try { args = JSON.parse(job.args_json); } catch (e) {}

        const statusColors = {
            pending: 'bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300',
            processing: 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300',
            completed: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-300',
            failed: 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-300',
        };
        const statusClass = statusColors[job.status] || 'bg-slate-100 text-slate-700';
        const timeStr = formatTimeShort(job.completed_at || job.started_at || job.created_at);
        const errorStr = job.error_message ? `<span class="text-red-400 truncate max-w-[200px] inline-block" title="${escapeHtml(job.error_message)}">${escapeHtml(job.error_message.slice(0, 60))}</span>` : '';

        return `<tr class="bg-white dark:bg-slate-800 border-b dark:border-slate-700">
            <td class="px-3 py-1.5 font-mono">${job.id}</td>
            <td class="px-3 py-1.5">${job.type}</td>
            <td class="px-3 py-1.5">${args.model_id || '-'}</td>
            <td class="px-3 py-1.5 font-mono">${args.run_id ? args.run_id.split('_').slice(1).join(' ') : '-'}</td>
            <td class="px-3 py-1.5">${args.variable_id || '-'}</td>
            <td class="px-3 py-1.5">H${args.forecast_hour ?? '-'}</td>
            <td class="px-3 py-1.5"><span class="px-2 py-0.5 rounded text-[10px] font-semibold ${statusClass}">${job.status}</span></td>
            <td class="px-3 py-1.5 font-mono">${timeStr}</td>
            <td class="px-3 py-1.5">${errorStr}</td>
        </tr>`;
    }).join('');
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

function renderLogs(lines) {
    const container = document.getElementById('logsContainer');
    if (!container) return;

    container.innerHTML = lines.map(line => {
        let className = '';
        if (line.includes('[ERROR]')) className = 'text-red-400';
        if (line.includes('[WARNING]')) className = 'text-amber-400';
        return `<div class="${className}">${escapeHtml(line)}</div>`;
    }).join('');

    container.scrollTop = container.scrollHeight;
}

if (typeof module !== 'undefined') {
    module.exports = { initStatusPage, renderRunGrid, renderDiskUsage, renderLogs, renderSchedulerStats };
}
