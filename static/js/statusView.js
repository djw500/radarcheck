async function fetchJSON(url) {
    const res = await fetch(url);
    if (!res.ok) throw new Error(await res.text());
    return res.json();
}

async function initStatusPage() {
    try {
        const data = await fetchJSON('/api/status/summary');
        renderStatusGrid(data.cache_status);
        renderDiskUsage(data.disk_usage);
        renderSchedulerStats(data.scheduler_status);
        
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

function renderDiskUsage(usage) {
    const elTotal = document.getElementById('diskTotal');
    const elGribs = document.getElementById('diskGribs');
    const elTiles = document.getElementById('diskTiles');
    
    if (elTotal) elTotal.textContent = formatBytes(usage.total);
    if (elGribs) elGribs.textContent = formatBytes(usage.gribs.total);
    if (elTiles) elTiles.textContent = formatBytes(usage.tiles.total);
}

function renderStatusGrid(cacheStatus) {
    const container = document.getElementById('statusGrid');
    if (!container) return;
    
    const models = Object.keys(cacheStatus).sort();
    const runIds = new Set();
    
    models.forEach(m => {
        Object.keys(cacheStatus[m].runs).forEach(r => runIds.add(r));
    });
    
    // Sort runs descending (newest first)
    const sortedRuns = Array.from(runIds).sort().reverse().slice(0, 50); // Limit to 50
    
    let html = '<table class="w-full text-sm text-left text-slate-500 dark:text-slate-400">';
    html += '<thead class="text-xs text-slate-700 uppercase bg-slate-50 dark:bg-slate-700 dark:text-slate-400"><tr>';
    html += '<th class="px-6 py-3">Run (Date/Hour)</th>';
    
    models.forEach(m => {
        html += `<th class="px-6 py-3 text-center">${cacheStatus[m].name}</th>`;
    });
    html += '</tr></thead><tbody>';
    
    sortedRuns.forEach(runId => {
        // Parse runId: run_YYYYMMDD_HH
        const parts = runId.split('_');
        const displayRun = `${parts[1]} ${parts[2]}Z`;
        
        html += `<tr class="bg-white border-b dark:bg-slate-800 dark:border-slate-700">`;
        html += `<td class="px-6 py-4 font-medium text-slate-900 dark:text-white whitespace-nowrap">${displayRun}</td>`;
        
        models.forEach(m => {
            const runData = cacheStatus[m].runs[runId];
            let cellContent = '-';
            let cellClass = 'bg-slate-100 dark:bg-slate-900'; // Default/Empty
            
            if (runData) {
                if (runData.status === 'complete') {
                    cellClass = 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900 dark:text-emerald-300';
                    cellContent = `✓ (${runData.hours_present}h)`;
                } else if (runData.status === 'partial') {
                    cellClass = 'bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-300';
                    cellContent = `⚠ (${runData.hours_present}/${runData.expected_hours}h)`;
                } else {
                    // Empty or other
                    cellContent = 'Empty';
                }
            }
            
            html += `<td class="px-6 py-4 text-center ${cellClass}">${cellContent}</td>`;
        });
        html += '</tr>';
    });
    
    html += '</tbody></table>';
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
    module.exports = { initStatusPage, renderStatusGrid, renderDiskUsage, renderLogs, renderSchedulerStats };
}