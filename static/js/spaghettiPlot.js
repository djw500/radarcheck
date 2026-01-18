let spaghettiChart = null;

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
        return {
            label: new Date(run.init_time).toLocaleString(),
            data: hours.map(hour => ({
                x: hour,
                y: valueMap.get(hour) ?? null
            })),
            borderColor: `rgba(0, 64, 128, ${alpha})`,
            backgroundColor: 'transparent',
            tension: 0.3
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
        tension: 0.1
    });

    datasets.push({
        label: 'Mean + Std Dev',
        data: hours.map((hour, idx) => ({ x: hour, y: meanValues[idx] != null && stdValues[idx] != null ? meanValues[idx] + stdValues[idx] : null })),
        borderColor: 'rgba(0,128,0,0.6)',
        borderDash: [2, 4],
        backgroundColor: 'transparent',
        tension: 0.1
    });

    datasets.push({
        label: 'Mean - Std Dev',
        data: hours.map((hour, idx) => ({ x: hour, y: meanValues[idx] != null && stdValues[idx] != null ? meanValues[idx] - stdValues[idx] : null })),
        borderColor: 'rgba(128,0,0,0.6)',
        borderDash: [2, 4],
        backgroundColor: 'transparent',
        tension: 0.1
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
                        text: 'Forecast Hour'
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
                    intersect: false
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
