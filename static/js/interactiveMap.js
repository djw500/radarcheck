class WeatherMap {
    constructor(containerId, options = {}) {
        this.map = L.map(containerId, {
            center: [options.centerLat || 40.0, options.centerLon || -75.0],
            zoom: options.zoom || 8,
            maxBounds: options.bounds
        });

        this.baseLayers = {
            'Streets': L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
                attribution: '&copy; OpenStreetMap contributors'
            }),
            'Satellite': L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
                attribution: '&copy; Esri'
            }),
            'Terrain': L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png', {
                attribution: '&copy; OpenTopoMap'
            })
        };

        this.overlayLayers = {};
        const overlayConfigs = options.overlayLayers || {};
        Object.values(overlayConfigs).forEach(layer => {
            if (!layer.url) {
                return;
            }
            this.overlayLayers[layer.name] = L.tileLayer(layer.url, {
                attribution: layer.attribution || '',
                maxZoom: layer.max_zoom || 12,
                minZoom: layer.min_zoom || 4,
                opacity: layer.opacity ?? 0.7
            });
        });

        this.baseLayers['Streets'].addTo(this.map);
        this.weatherLayer = null;
        this.forecastOverlayLayer = null;
        this.forecastOverlayLabel = null;
        this.currentHour = 1;
        this.locationId = null;
        this.modelId = null;
        this.runId = null;
        this.variableId = null;

        this.map.on('click', (e) => this.onMapClick(e));
        this.layerControl = L.control.layers(this.baseLayers, this.overlayLayers, {position: 'topright'});
        this.layerControl.addTo(this.map);
    }

    setWeatherLayer(locationId, modelId, runId, variableId, hour) {
        if (this.weatherLayer) {
            this.map.removeLayer(this.weatherLayer);
        }

        const authParam = typeof apiKeyParam !== 'undefined' ? apiKeyParam : '';
        const tileUrl = `/tiles/${locationId}/${modelId}/${runId}/${variableId}/${hour}/{z}/{x}/{y}.png${authParam}`;
        this.weatherLayer = L.tileLayer(tileUrl, {
            opacity: 0.7,
            maxZoom: 12,
            minZoom: 4
        }).addTo(this.map);

        this.currentHour = hour;
        this.locationId = locationId;
        this.modelId = modelId;
        this.runId = runId;
        this.variableId = variableId;
    }

    async onMapClick(e) {
        if (!this.locationId) {
            return;
        }
        const {lat, lng} = e.latlng;
        const authParam = typeof apiKeyParam !== 'undefined' ? apiKeyParam : '';
        const queryPrefix = authParam ? `${authParam}&` : '?';
        const response = await fetch(
            `/api/value/${this.locationId}/${this.modelId}/${this.runId}/${this.variableId}/${this.currentHour}${queryPrefix}lat=${lat}&lon=${lng}`
        );

        if (response.ok) {
            const data = await response.json();
            L.popup()
                .setLatLng(e.latlng)
                .setContent(`
                    <strong>${data.variable}</strong><br>
                    Value: ${data.value?.toFixed(1) ?? 'N/A'} ${data.units}<br>
                    Hour +${data.forecast_hour}
                `)
                .openOn(this.map);
        }
    }

    setOpacity(opacity) {
        if (this.weatherLayer) {
            this.weatherLayer.setOpacity(opacity);
        }
    }

    setOverlayLayer(locationId, modelId, runId, variableId, hour, label, opacity = 0.5) {
        if (this.forecastOverlayLayer) {
            this.map.removeLayer(this.forecastOverlayLayer);
            if (this.layerControl && this.forecastOverlayLabel) {
                this.layerControl.removeLayer(this.forecastOverlayLayer);
            }
        }

        if (!variableId) {
            this.forecastOverlayLayer = null;
            this.forecastOverlayLabel = null;
            return;
        }

        const authParam = typeof apiKeyParam !== 'undefined' ? apiKeyParam : '';
        const tileUrl = `/tiles/${locationId}/${modelId}/${runId}/${variableId}/${hour}/{z}/{x}/{y}.png${authParam}`;
        this.forecastOverlayLayer = L.tileLayer(tileUrl, {
            opacity: opacity,
            maxZoom: 12,
            minZoom: 4
        });
        this.forecastOverlayLabel = label || `Overlay: ${variableId}`;
        this.forecastOverlayLayer.addTo(this.map);
        if (this.layerControl) {
            this.layerControl.addOverlay(this.forecastOverlayLayer, this.forecastOverlayLabel);
        }
    }

    setOverlayOpacity(opacity) {
        if (this.forecastOverlayLayer) {
            this.forecastOverlayLayer.setOpacity(opacity);
        }
    }
}

if (typeof module !== 'undefined') {
    module.exports = { WeatherMap };
}
