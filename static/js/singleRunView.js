// Single Run View
function initSingleRunView() {
    const slider = document.getElementById('timeSlider');
    const timeDisplay = document.getElementById('timeDisplay');
    const forecastImage = document.getElementById('forecastImage');
    const loading = document.getElementById('loading');
    const playButton = document.getElementById('playButton');

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

    // Preload first few frames immediately
    Promise.all([1, 2, 3].map(preloadImage)).then(() => {
        // Then load the rest in background
        for (let hour = 4; hour <= maxForecastHours; hour++) {
            preloadImage(hour);
        }
    });

    function updateDisplay(hour) {
        timeDisplay.textContent = `Hour +${hour}`;
        if (images[hour - 1]) {
            forecastImage.src = images[hour - 1].src;
        } else {
            forecastImage.src = `/frame/${locationId}/${modelId}/${runId}/${variableId}/${hour}${apiKeyParam}`;
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
            }, 500);
            playButton.textContent = 'Pause';
        }
        isPlaying = !isPlaying;
    });
}
