// Initialize everything when the page loads
document.addEventListener('DOMContentLoaded', function () {
    // If API key is present, update the initial image and run selector links
    if (apiKey) {
        // Fix initial forecast image
        const forecastImage = document.getElementById('forecastImage');
        if (forecastImage && forecastImage.src) {
            forecastImage.src = forecastImage.src + apiKeyParam;
        }

        // Fix run selector links to preserve API key
        document.querySelectorAll('.run-selector a').forEach(link => {
            const url = new URL(link.href);
            url.searchParams.set('api_key', apiKey);
            link.href = url.toString();
        });
    }

    initViewSwitching();
    initSingleRunView();

    // Pre-create the timeline structure when the page loads
    setTimeout(createTimeline, 100);
});
