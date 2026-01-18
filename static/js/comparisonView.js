let comparisonInitialized = false;

function initComparisonView() {
    if (comparisonInitialized) {
        return;
    }
    comparisonInitialized = true;

    const primaryContainer = document.getElementById('primaryCompareMap');
    const secondaryContainer = document.getElementById('secondaryCompareMap');
    if (!primaryContainer || !secondaryContainer || typeof WeatherMap === 'undefined') {
        return;
    }

    const compareModelSelect = document.getElementById('compareModelSelect');
    const compareVariableSelect = document.getElementById('compareVariableSelect');
    const compareRunSelect = document.getElementById('compareRunSelect');
    const compareSlider = document.getElementById('compareTimeSlider');
    const compareDisplay = document.getElementById('compareTimeDisplay');
    const comparePlayButton = document.getElementById('comparePlayButton');
    if (!compareModelSelect || !compareVariableSelect || !compareRunSelect || !compareSlider || !compareDisplay || !comparePlayButton) {
        return;
    }

    const overlayConfig = typeof overlayLayers !== 'undefined' ? overlayLayers : {};
    const primaryMap = new WeatherMap('primaryCompareMap', {
        centerLat: mapCenter.lat,
        centerLon: mapCenter.lon,
        zoom: mapCenter.zoom,
        overlayLayers: overlayConfig
    });
    const secondaryMap = new WeatherMap('secondaryCompareMap', {
        centerLat: mapCenter.lat,
        centerLon: mapCenter.lon,
        zoom: mapCenter.zoom,
        overlayLayers: overlayConfig
    });

    primaryMap.setWeatherLayer(locationId, modelId, runId, variableId, 1);

    const apiSuffix = typeof apiKeyParam !== 'undefined' ? apiKeyParam : '';

    async function loadRunsForModel(model) {
        const response = await fetch(`/api/runs/${locationId}/${model}${apiSuffix}`);
        if (!response.ok) {
            return [];
        }
        return response.json();
    }

    function updateSliderMax(model) {
        if (typeof modelConfig !== 'undefined' && modelConfig[model]) {
            compareSlider.max = modelConfig[model].max_forecast_hours;
        }
    }

    async function populateRuns(model) {
        const runs = await loadRunsForModel(model);
        compareRunSelect.innerHTML = '';
        runs.forEach(run => {
            const option = document.createElement('option');
            option.value = run.run_id;
            option.textContent = run.init_time;
            compareRunSelect.appendChild(option);
        });
        if (runs.length > 0) {
            compareRunSelect.value = runs[0].run_id;
        }
    }

    function updateComparisonLayer(hour) {
        const compareModel = compareModelSelect.value;
        const compareVariable = compareVariableSelect.value;
        const compareRun = compareRunSelect.value;
        if (!compareRun) {
            return;
        }
        secondaryMap.setWeatherLayer(locationId, compareModel, compareRun, compareVariable, hour);
    }

    function updateBothMaps(hour) {
        compareDisplay.textContent = `Hour +${hour}`;
        primaryMap.setWeatherLayer(locationId, modelId, runId, variableId, hour);
        updateComparisonLayer(hour);
    }

    compareSlider.addEventListener('input', () => {
        updateBothMaps(parseInt(compareSlider.value, 10));
    });

    compareModelSelect.addEventListener('change', async () => {
        updateSliderMax(compareModelSelect.value);
        compareSlider.value = 1;
        await populateRuns(compareModelSelect.value);
        updateBothMaps(parseInt(compareSlider.value, 10));
    });

    compareVariableSelect.addEventListener('change', () => {
        updateBothMaps(parseInt(compareSlider.value, 10));
    });

    compareRunSelect.addEventListener('change', () => {
        updateBothMaps(parseInt(compareSlider.value, 10));
    });

    let comparePlaying = false;
    let compareInterval;
    comparePlayButton.addEventListener('click', () => {
        if (comparePlaying) {
            clearInterval(compareInterval);
            comparePlayButton.textContent = 'Play';
        } else {
            compareInterval = setInterval(() => {
                let hour = parseInt(compareSlider.value, 10);
                const maxHour = parseInt(compareSlider.max, 10);
                hour = hour >= maxHour ? 1 : hour + 1;
                compareSlider.value = hour;
                updateBothMaps(hour);
            }, 500);
            comparePlayButton.textContent = 'Pause';
        }
        comparePlaying = !comparePlaying;
    });

    updateSliderMax(compareModelSelect.value);
    populateRuns(compareModelSelect.value).then(() => {
        updateBothMaps(1);
    });
}

if (typeof module !== 'undefined') {
    module.exports = { initComparisonView };
}
