const { initSingleRunView } = require('../../static/js/singleRunView');

beforeEach(() => {
    document.body.innerHTML = `
        <input id="timeSlider" type="range" value="1" />
        <span id="timeDisplay"></span>
        <img id="forecastImage" />
        <div id="loading"></div>
        <button id="playButton"></button>
        <input id="opacitySlider" type="range" value="70" />
        <div id="weatherMap"></div>
    `;

    global.locationId = 'philly';
    global.modelId = 'hrrr';
    global.runId = 'run_20240101_00';
    global.variableId = 'refc';
    global.maxForecastHours = 3;
    global.apiKeyParam = '';
    global.mapCenter = { lat: 40, lon: -75, zoom: 8 };
    global.loadPreferences = () => ({ playbackSpeed: 500 });

    global.WeatherMap = class {
        constructor() {
            this.layers = [];
        }
        setWeatherLayer() {
            this.layers.push('layer');
        }
        setOpacity() {}
    };

    global.Image = class {
        constructor() {
            setTimeout(() => this.onload && this.onload(), 0);
        }
        set src(value) {
            this._src = value;
        }
        get src() {
            return this._src;
        }
    };
});

test('updates display when slider moves', () => {
    initSingleRunView();
    const slider = document.getElementById('timeSlider');
    const display = document.getElementById('timeDisplay');

    slider.value = '2';
    slider.dispatchEvent(new Event('input'));

    expect(display.textContent).toBe('Hour +2');
});
