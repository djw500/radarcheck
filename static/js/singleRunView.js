// Single Run View
function initSingleRunView() {
    const slider = document.getElementById('timeSlider');
    const timeDisplay = document.getElementById('timeDisplay');
    const forecastImage = document.getElementById('forecastImage');
    const loading = document.getElementById('loading');
    const playButton = document.getElementById('playButton');
    const opacitySlider = document.getElementById('opacitySlider');
    const overlayVariableSelect = document.getElementById('overlayVariableSelect');
    const overlayOpacitySlider = document.getElementById('overlayOpacitySlider');
    const preferences = loadPreferences();

    let weatherMap = null;
    if (document.getElementById('weatherMap') && typeof WeatherMap !== 'undefined') {
        weatherMap = new WeatherMap('weatherMap', {
            centerLat: mapCenter.lat,
            centerLon: mapCenter.lon,
            zoom: mapCenter.zoom,
            overlayLayers: typeof overlayLayers !== 'undefined' ? overlayLayers : {}
        });
        weatherMap.setWeatherLayer(locationId, modelId, runId, variableId, 1);
        window.weatherMap = weatherMap;
    } else if (forecastImage) {
        forecastImage.classList.add('visible');
    }

    let isPlaying = false;
    let playInterval;

    // Preload images
    const images = new Array(maxForecastHours);
    function preloadImage(hour) {
        return new Promise((resolve, reject) => {
            const img = new Image();
            img.onload = () => {
                images[hour - 1] = img;
                resolve();
            };
            img.onerror = reject;
            img.src = `/frame/${locationId}/${modelId}/${runId}/${variableId}/${hour}${apiKeyParam}`;
        });
    }

    const preloadLimit = Math.min(maxForecastHours, 48);

    // Preload first few frames immediately
    Promise.all([1, 2, 3].map(preloadImage)).then(() => {
        // Then load the rest in background (cap for very long models)
        for (let hour = 4; hour <= preloadLimit; hour++) {
            preloadImage(hour);
        }
    });

    function updateDisplay(hour) {
        timeDisplay.textContent = `Hour +${hour}`;
        if (weatherMap) {
            weatherMap.setWeatherLayer(locationId, modelId, runId, variableId, hour);
            if (overlayVariableSelect && overlayVariableSelect.value) {
                const label = overlayVariableSelect.options[overlayVariableSelect.selectedIndex]?.textContent?.trim();
                weatherMap.setOverlayLayer(
                    locationId,
                    modelId,
                    runId,
                    overlayVariableSelect.value,
                    hour,
                    label,
                    overlayOpacitySlider ? overlayOpacitySlider.value / 100 : 0.5
                );
            }
        }
        if (forecastImage) {
            if (images[hour - 1]) {
                forecastImage.src = images[hour - 1].src;
            } else {
                forecastImage.src = `/frame/${locationId}/${modelId}/${runId}/${variableId}/${hour}${apiKeyParam}`;
            }
        }
    }

    slider.addEventListener('input', () => {
        const hour = parseInt(slider.value);
        updateDisplay(hour);
    });

    playButton.addEventListener('click', () => {
        if (isPlaying) {
            clearInterval(playInterval);
            playButton.textContent = 'Play';
        } else {
            playInterval = setInterval(() => {
                let hour = parseInt(slider.value);
                hour = hour >= maxForecastHours ? 1 : hour + 1;
                slider.value = hour;
                updateDisplay(hour);
            }, preferences.playbackSpeed || 500);
            playButton.textContent = 'Pause';
        }
        isPlaying = !isPlaying;
    });

    if (opacitySlider && weatherMap) {
        opacitySlider.addEventListener('input', () => {
            weatherMap.setOpacity(opacitySlider.value / 100);
        });
    }

    if (overlayVariableSelect && weatherMap) {
        overlayVariableSelect.addEventListener('change', () => {
            if (!overlayVariableSelect.value) {
                weatherMap.setOverlayLayer(locationId, modelId, runId, '', slider.value, '', 0);
                return;
            }
            const label = overlayVariableSelect.options[overlayVariableSelect.selectedIndex]?.textContent?.trim();
            weatherMap.setOverlayLayer(
                locationId,
                modelId,
                runId,
                overlayVariableSelect.value,
                slider.value,
                label,
                overlayOpacitySlider ? overlayOpacitySlider.value / 100 : 0.5
            );
        });
    }

    if (overlayOpacitySlider && weatherMap) {
        overlayOpacitySlider.addEventListener('input', () => {
            weatherMap.setOverlayOpacity(overlayOpacitySlider.value / 100);
        });
    }
}

if (typeof module !== 'undefined') {
    module.exports = { initSingleRunView };
}
