// Initialize everything when the page loads
document.addEventListener('DOMContentLoaded', function () {
    const prefs = loadPreferences();

    if (document.querySelector('.locations-container') && prefs?.location) {
        const target = document.querySelector(`[data-location-id=\"${prefs.location}\"]`);
        if (target) {
            window.location.href = target.href;
            return;
        }
    }

    if (typeof locationId !== 'undefined') {
        savePreferences({
            ...prefs,
            location: locationId,
            model: modelId,
            variable: variableId
        });
    }

    const apiKeyValue = typeof apiKey !== 'undefined' ? apiKey : '';
    const apiKeyQuery = typeof apiKeyParam !== 'undefined' ? apiKeyParam : '';

    // If API key is present, update the initial image and run selector links
    if (apiKeyValue) {
        // Fix initial forecast image
        const forecastImage = document.getElementById('forecastImage');
        if (forecastImage && forecastImage.src) {
            forecastImage.src = forecastImage.src + apiKeyQuery;
        }

        // Fix run selector links to preserve API key
        document.querySelectorAll('.run-selector a').forEach(link => {
            const url = new URL(link.href);
            url.searchParams.set('api_key', apiKeyValue);
            link.href = url.toString();
        });
    }

    const modelSelect = document.getElementById('modelSelect');
    const variableSelect = document.getElementById('variableSelect');

    function updateLocationParams(param, value) {
        const url = new URL(window.location.href);
        url.searchParams.set(param, value);
        window.location.href = url.toString();
    }

    if (modelSelect) {
        modelSelect.addEventListener('change', () => {
            savePreferences({
                ...prefs,
                location: typeof locationId !== 'undefined' ? locationId : prefs.location,
                model: modelSelect.value,
                variable: variableSelect?.value || prefs.variable,
            });
            updateLocationParams('model', modelSelect.value);
        });
    }

    if (variableSelect) {
        variableSelect.addEventListener('change', () => {
            savePreferences({
                ...prefs,
                location: typeof locationId !== 'undefined' ? locationId : prefs.location,
                model: modelSelect?.value || prefs.model,
                variable: variableSelect.value,
            });
            updateLocationParams('variable', variableSelect.value);
        });
    }

    initViewSwitching();
    initSingleRunView();

    // Pre-create the timeline structure when the page loads
    setTimeout(createTimeline, 100);
});
