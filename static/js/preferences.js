const STORAGE_KEY = 'radarcheck_preferences';

function savePreferences(prefs) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs));
}

function loadPreferences() {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (!stored) {
        return {
            location: 'philly',
            model: 'hrrr',
            variable: 'refc',
            playbackSpeed: 500
        };
    }
    try {
        return JSON.parse(stored);
    } catch (error) {
        return {
            location: 'philly',
            model: 'hrrr',
            variable: 'refc',
            playbackSpeed: 500
        };
    }
}

if (typeof module !== 'undefined') {
    module.exports = { loadPreferences, savePreferences };
}
