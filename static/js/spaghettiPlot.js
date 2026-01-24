let spaghettiChart = null;

function formatRunLabel(initIso, latestInitMs) {
    try {
        const dt = new Date(initIso);
        const utcHour = dt.getUTCHours().toString().padStart(2, '0');
        const mon = dt.toLocaleString('en-US', { month: 'short' });
        const day = dt.getDate();
        const labelBase = `${mon} ${day} ${utcHour}Z`;
        const ageHrs = (latestInitMs - dt.getTime()) / 3600000;
        const ageLabel = ageHrs < 1 ? 'now' : `${Math.round(ageHrs)}h ago`;
        return { text: `${labelBase} • ${ageLabel}`, ageHrs };
    } catch (e) {
        return { text: initIso, ageHrs: Infinity };
    }
}

function buildRunControls(datasets) {
    const container = document.getElementById('spaghettiControls');
    if (!container) {
        return;
    }
    container.innerHTML = '';
    datasets.forEach((dataset) => {
        const label = document.createElement('label');
        label.className = 'spaghetti-toggle';
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.checked = true;
        checkbox.addEventListener('change', () => {
            dataset.hidden = !checkbox.checked;
            spaghettiChart.update();
        });
        label.appendChild(checkbox);
        const text = document.createElement('span');
        text.textContent = dataset.label;
        label.appendChild(text);
        container.appendChild(label);
    });
}

function updateStats(meanValues, stdValues, units) {
    const stats = document.getElementById('spaghettiStats');
    if (!stats) {
        return;
    }
    const latestMean = meanValues[meanValues.length - 1];
    const latestStd = stdValues[stdValues.length - 1];
    stats.textContent = `Latest mean: ${latestMean?.toFixed(2) ?? 'N/A'} ${units || ''} | Std dev: ${latestStd?.toFixed(2) ?? 'N/A'} ${units || ''}`;
}

function calculateSummary(hours, datasets) {
    const meanValues = [];
    const stdValues = [];
    for (let i = 0; i < hours.length; i++) {
        const values = datasets.map(dataset => dataset.data[i]?.y).filter(value => value !== null && value !== undefined);
        if (!values.length) {
            meanValues.push(null);
            stdValues.push(null);
            continue;
        }
        const mean = values.reduce((sum, value) => sum + value, 0) / values.length;
        const variance = values.reduce((sum, value) => sum + Math.pow(value - mean, 2), 0) / values.length;
        meanValues.push(mean);
        stdValues.push(Math.sqrt(variance));
    }
    return { meanValues, stdValues };
}

async function fetchCenterValues() {
    const response = await fetch(`/api/center_values/${locationId}/${modelId}${apiKeyParam}`);
    if (!response.ok) {
        return [];
    }
    return response.json();
}

async function createSpaghettiPlot() {
    if (spaghettiChart) {
        spaghettiChart.destroy();
    }

    const centerValues = await fetchCenterValues();
    const valuesByRun = new Map(centerValues.map(run => [run.run_id, run]));

    const hours = Array.from({ length: maxForecastHours }, (_, i) => i + 1);

    const latestTime = Math.max(...timelineData.map(run => new Date(run.init_time).getTime()));

    const datasets = timelineData.map((run) => {
        const runData = valuesByRun.get(run.run_id);
        const values = (runData?.values || []).sort((a, b) => a.forecast_hour - b.forecast_hour);
        const valueMap = new Map(values.map(entry => [entry.forecast_hour, entry.value]));
        const ageHours = (latestTime - new Date(run.init_time).getTime()) / 3600000;
        const alpha = Math.max(0.2, 1 - ageHours / 24);
        const labelInfo = formatRunLabel(run.init_time, latestTime);
        const isLatest = ageHours === 0;
        const baseColor = `rgba(0, 64, 128, ${alpha})`;
        return {
            label: isLatest ? `${labelInfo.text} • LATEST` : labelInfo.text,
            data: hours.map(hour => ({
                x: hour,
                y: valueMap.get(hour) ?? null
            })),
            borderColor: baseColor,
            backgroundColor: 'transparent',
            tension: 0.3,
            borderWidth: isLatest ? 3 : 1.5,
            pointRadius: 0,
            _initTime: run.init_time
        };
    });

    const { meanValues, stdValues } = calculateSummary(hours, datasets);
    const statsUnits = centerValues[0]?.units || '';

    datasets.push({
        label: 'Mean',
        data: hours.map((hour, idx) => ({ x: hour, y: meanValues[idx] })),
        borderColor: 'rgba(0,0,0,0.8)',
        borderDash: [5, 5],
        backgroundColor: 'transparent',
        tension: 0.1,
        pointRadius: 0,
        borderWidth: 2
    });

    datasets.push({
        label: 'Mean + Std Dev',
        data: hours.map((hour, idx) => ({ x: hour, y: meanValues[idx] != null && stdValues[idx] != null ? meanValues[idx] + stdValues[idx] : null })),
        borderColor: 'rgba(0,128,0,0.6)',
        borderDash: [2, 4],
        backgroundColor: 'transparent',
        tension: 0.1,
        pointRadius: 0
    });

    datasets.push({
        label: 'Mean - Std Dev',
        data: hours.map((hour, idx) => ({ x: hour, y: meanValues[idx] != null && stdValues[idx] != null ? meanValues[idx] - stdValues[idx] : null })),
        borderColor: 'rgba(128,0,0,0.6)',
        borderDash: [2, 4],
        backgroundColor: 'transparent',
        tension: 0.1,
        pointRadius: 0
    });

    const ctx = document.getElementById('spaghettiChart').getContext('2d');
    spaghettiChart = new Chart(ctx, {
        type: 'line',
        data: {
            datasets: datasets
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                x: {
                    type: 'linear',
                    title: {
                        display: true,
                        text: 'Forecast Hour (Day markers every 24h)'
                    },
                    ticks: {
                        callback: function(value) {
                            if (value % 24 === 0 && value !== 0) {
                                const dayNum = value / 24;
                                return [value.toString(), `Day ${dayNum}`];
                            }
                            return value.toString();
                        },
                        maxTicksLimit: 20
                    },
                    grid: {
                        color: function(ctx) {
                            const x = ctx.tick.value;
                            return (x % 24 === 0 && x !== 0) ? 'rgba(0,0,0,0.25)' : 'rgba(0,0,0,0.08)';
                        }
                    }
                },
                y: {
                    title: {
                        display: true,
                        text: `Center Value ${statsUnits ? `(${statsUnits})` : ''}`
                    }
                }
            },
            plugins: {
                title: {
                    display: true,
                    text: 'Forecast Comparison'
                },
                tooltip: {
                    mode: 'nearest',
                    intersect: false,
                    callbacks: {
                        label: function(context) {
                            const ds = context.dataset;
                            const hour = context.parsed.x;
                            let label = ds.label ? ds.label + ': ' : '';
                            label += `H+${hour}`;
                            if (ds._initTime) {
                                const valid = new Date(new Date(ds._initTime).getTime() + hour * 3600000);
                                const validStr = valid.toLocaleString('en-US', { weekday: 'short', hour: 'numeric', hour12: true });
                                label += ` (valid ${validStr})`;
                            }
                            return label;
                        }
                    }
                },
                zoom: {
                    zoom: {
                        wheel: { enabled: true },
                        pinch: { enabled: true },
                        mode: 'x'
                    },
                    pan: {
                        enabled: true,
                        mode: 'x'
                    }
                }
            }
        }
    });

    buildRunControls(datasets.filter(dataset => !dataset.label.startsWith('Mean')));
    updateStats(meanValues, stdValues, statsUnits);
}

if (typeof module !== 'undefined') {
    module.exports = { createSpaghettiPlot, calculateSummary };
}
