async function fetchJSON(url) {
    const res = await fetch(url);
    if (!res.ok) throw new Error(await res.text());
    return res.json();
}

async function initStatusPage() {
    try {
        // Fetch both endpoints in parallel
        const [summaryData, scheduledData] = await Promise.all([
            fetchJSON('/api/status/summary'),
            fetchJSON('/api/status/scheduled')
        ]);

        renderScheduledRuns(scheduledData.runs);
        renderDiskUsage(summaryData.disk_usage);
        renderSchedulerStats(summaryData.scheduler_status);

        await loadLogs();

        const refreshBtn = document.getElementById('refreshLogsBtn');
        if (refreshBtn) {
            refreshBtn.onclick = loadLogs;
        }
    } catch (e) {
        console.error("Failed to load status:", e);
    }
}

async function loadLogs() {
    try {
        const logs = await fetchJSON('/api/status/logs?lines=50');
        renderLogs(logs.lines);
    } catch (e) {
        console.error("Failed to load logs:", e);
    }
}

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
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false
    });
}

function formatTimeShort(isoStr) {
    if (!isoStr) return '-';
    const d = new Date(isoStr);
    return d.toLocaleString('en-US', {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        hour12: false
    }) + 'Z';
}

function renderDiskUsage(usage) {
    const elTotal = document.getElementById('diskTotal');
    const elGribs = document.getElementById('diskGribs');
    const elTiles = document.getElementById('diskTiles');

    if (elTotal) elTotal.textContent = formatBytes(usage.total);
    if (elGribs) elGribs.textContent = formatBytes(usage.gribs.total);
    if (elTiles) elTiles.textContent = formatBytes(usage.tiles.total);
}

function renderScheduledRuns(runs) {
    const container = document.getElementById('scheduledRunsGrid') || document.getElementById('statusGrid');
    if (!container) return;

    if (!runs || runs.length === 0) {
        container.innerHTML = '<p class="text-slate-500">No scheduled runs found.</p>';
        return;
    }

    // Group by model
    const byModel = {};
    runs.forEach(run => {
        if (!byModel[run.model_id]) {
            byModel[run.model_id] = {
                name: run.model_name,
                runs: []
            };
        }
        byModel[run.model_id].runs.push(run);
    });

    const modelOrder = ['hrrr', 'nam_nest', 'gfs', 'nbm', 'ecmwf_hres'];

    let html = '';

    // Utility to build a per-model table with columns for union of expected hours
    function buildModelTable(modelId, modelData) {
        // Compute union of expected hours across runs for this model
        const columns = Array.from(new Set(modelData.runs.flatMap(r => r.expected_hours))).sort((a,b)=>a-b);
        const colWidth = 'w-6';
        let t = '';
        t += `<div class="mb-6">`;
        t += `<div class="text-sm font-semibold mb-2 text-slate-900 dark:text-white">${modelData.name}</div>`;
        t += '<div class="overflow-x-auto"><table class="min-w-full text-xs text-left text-slate-500 dark:text-slate-400">';
        t += '<thead class="text-[10px] uppercase bg-slate-50 dark:bg-slate-700 dark:text-slate-300">';
        t += '<tr>';
        t += '<th class="px-2 py-2 sticky left-0 bg-slate-50 dark:bg-slate-700">Run</th>';
        columns.forEach(h => {
            t += `<th class="px-1 py-2 text-center ${colWidth}">H${h}</th>`;
        });
        t += '<th class="px-2 py-2 text-center">Extra</th>';
        t += '<th class="px-2 py-2 text-center">Status</th>';
        t += '</tr></thead><tbody>';

        modelData.runs.forEach(run => {
            const parts = run.run_id.split('_');
            const displayRun = `${parts[1]} ${parts[2]}Z`;
            const expectedSet = new Set(run.expected_hours);
            const cachedSet = new Set(run.cached_hours);
            const extraHours = run.cached_hours.filter(h => !expectedSet.has(h));

            // Status styling
            let statusClass, statusIcon;
            if (run.status === 'complete') {
                statusClass = 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900 dark:text-emerald-300';
                statusIcon = 'check_circle';
            } else if (run.status === 'partial') {
                statusClass = 'bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-300';
                statusIcon = 'warning';
            } else {
                statusClass = 'bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-300';
                statusIcon = 'error';
            }

            t += '<tr class="bg-white dark:bg-slate-800 border-b dark:border-slate-700">';
            t += `<td class="px-2 py-2 font-mono text-[11px] sticky left-0 bg-white dark:bg-slate-800">${displayRun}</td>`;
            columns.forEach(h => {
                // Cell state: present/missing/not expected
                let cellClass = 'bg-slate-200 dark:bg-slate-700'; // not expected (default)
                if (expectedSet.has(h)) {
                    cellClass = cachedSet.has(h) ? 'bg-emerald-500' : 'bg-red-500';
                }
                t += `<td class="px-1 py-1 text-center"><span class="inline-block h-4 w-4 rounded ${cellClass}"></span></td>`;
            });
            t += `<td class="px-2 py-2 text-center font-mono">${extraHours.length}</td>`;
            t += `<td class="px-2 py-2 text-center"><span class="inline-flex items-center gap-1 px-2 py-1 rounded ${statusClass}"><span class="material-icons-outlined text-sm">${statusIcon}</span>${run.status}</span></td>`;
            t += '</tr>';
        });

        t += '</tbody></table></div>';
        t += '</div>';
        return t;
    }

    modelOrder.forEach(modelId => {
        const modelData = byModel[modelId];
        if (!modelData) return;
        html += buildModelTable(modelId, modelData);
    });

    container.innerHTML = html;
}

function renderLogs(lines) {
    const container = document.getElementById('logsContainer');
    if (!container) return;

    container.innerHTML = lines.map(line => {
        // Simple highlighting
        let className = "";
        if (line.includes('[ERROR]')) className = "text-red-400";
        if (line.includes('[WARNING]')) className = "text-amber-400";
        return `<div class="${className}">${line}</div>`;
    }).join('');

    container.scrollTop = container.scrollHeight;
}

function renderSchedulerStats(status) {
    if (!status) return;

    const elLast = document.getElementById('schedLastRun');
    const elNext = document.getElementById('schedNextRun');
    const elTargets = document.getElementById('schedTargets');

    if (elLast && status.last_run) elLast.textContent = new Date(status.last_run).toLocaleTimeString();
    if (elNext && status.next_run) elNext.textContent = new Date(status.next_run).toLocaleTimeString();

    if (elTargets) {
        if (status.targets && status.targets.length > 0) {
            elTargets.textContent = status.targets.join(', ');
        } else {
            elTargets.textContent = "None";
        }
    }
}

if (typeof module !== 'undefined') {
    module.exports = { initStatusPage, renderScheduledRuns, renderDiskUsage, renderLogs, renderSchedulerStats };
}
