#!/usr/bin/env python3
"""
HTML generator for Run8 Track Mapper.

Generates index.html with Folium base map and embedded JavaScript
for dynamic region loading.
"""

import folium
from pathlib import Path
from typing import List, Tuple, Optional

from config_parser import VisualizationConfig, ColorConfig


# Default map center (Southern California)
DEFAULT_CENTER = [34.9, -118.0]
DEFAULT_ZOOM = 9


def generate_color_config(colors: ColorConfig) -> str:
    """Generate JavaScript color configuration"""
    return f'''<script>
window.COLORS = {{
    track: '{colors.track}',
    trackSelected: '{colors.track_selected}',
    trackHover: '{colors.track_hover}',
    switch: '{colors.switch}',
    industryTrack: '{colors.industry_track}',
    signalAbsolute: '{colors.signal_absolute}',
    signalIntermediate: '{colors.signal_intermediate}',
    signalBorderSingle: '{colors.signal_border_single}',
    signalBorderStacked: '{colors.signal_border_stacked}',
    areaLabel: '{colors.area_label}',
    train: '{colors.train}',
    trainLoco: '{colors.train_loco}',
    trainOutline: '{colors.train_outline}'
}};
</script>'''


def generate_javascript() -> str:
    """Generate the JavaScript code for dynamic region management"""
    return '''
<script>
(function() {
    'use strict';

    // Use colors from window.COLORS (injected separately)
    const COLORS = window.COLORS;
    // Rail-vehicle line widths from [trains] config, with safe defaults.
    // car = min px floor; carM = real car width (m) the body widens to with zoom.
    const TRAIN_STYLE = window.TRAIN_STYLE || {car: 7, spine: 1.5, carM: 3.5, labelScaleM: 30};

    // ========================================
    // MapApp - Main Application State
    // ========================================
    const MapApp = {
        map: null,
        manifest: null,
        loadedRegions: new Map(),  // region_id -> {data, layers, visible}
        sectionIndex: new Map(),   // section_id -> {region_id, polyline, metadata}
        signalIndex: new Map(),    // signal_id -> {region_id, marker, metadata}
        industryIndex: [],         // [{region_id, data}]
        aiLocationIndex: [],       // [{region_id, data}]
        trainLayers: [],           // all rendered rail-vehicle polylines
        trainIndex: [],            // [{regionId, trainId, vehicle, layer}] for search
        industrySectionIds: new Set(),  // Set of "regionId_sectionId" keys for industry tracks

        // Local symbol filtering state
        localSymbolIndex: new Set(),    // Set of unique local symbols across all loaded regions
        currentLocalFilter: null,       // Currently selected local symbol (null = ALL)
        industryMarkers: new Map(),     // Map of "regionId_tag" -> {marker, data, regionId} for highlighting

        // Selection state
        selectedSections: new Map(),  // section_id -> polyline
        currentSelectionRegion: null,

        // Layer groups for overlays
        overlayStates: {
            signals: false,
            industries: false,
            aiLocations: false,
            tileBoundaries: false,
            trains: false
        },

        // Base map layers
        baseLayers: {},  // {name: layer}
        currentBaseLayer: 'OpenStreetMap',

        // UI elements
        regionCheckboxes: new Map(),
        searchDialog: null
    };

    // ========================================
    // Initialization
    // ========================================
    function init() {
        // Find the map object (Folium creates it with a specific name)
        const mapContainer = document.querySelector('.folium-map');
        if (!mapContainer) {
            setTimeout(init, 100);
            return;
        }

        // Get map variable name from container id
        const mapId = mapContainer.id;
        MapApp.map = window[mapId];

        if (!MapApp.map || typeof MapApp.map.eachLayer !== 'function') {
            setTimeout(init, 100);
            return;
        }

        console.log('Map initialized, loading manifest...');
        loadManifest();
    }

    async function loadManifest() {
        try {
            const response = await fetch('manifest.json');
            MapApp.manifest = await response.json();
            console.log('Manifest loaded:', MapApp.manifest.name);
            console.log('Regions:', MapApp.manifest.regions.length);

            setupUI();
            loadDefaultRegions();
        } catch (error) {
            console.error('Failed to load manifest:', error);
        }
    }

    // ========================================
    // UI Setup
    // ========================================
    function setupUI() {
        // Create base tile layers directly (Folium's show=False layers may not be on map)
        MapApp.baseLayers['OpenStreetMap'] = L.tileLayer(
            'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
            {attribution: 'OpenStreetMap', maxZoom: 22, maxNativeZoom: 19}
        );
        MapApp.baseLayers['Satellite'] = L.tileLayer(
            'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
            {attribution: 'Esri', maxZoom: 22, maxNativeZoom: 19}
        );
        MapApp.baseLayers['None'] = L.tileLayer('', {attribution: 'None', maxZoom: 22});

        // OpenRailwayMap: a transparent railway-line overlay rendered from the
        // same OSM data. It sits above the base map but below the track vectors
        // (tilePane is under overlayPane), so Run8 track draws on top of the
        // real rails for direct comparison. Toggled from the Base Map section.
        MapApp.railOverlay = L.tileLayer(
            'https://{s}.tiles.openrailwaymap.org/standard/{z}/{x}/{y}.png',
            {attribution: 'OpenRailwayMap | &copy; OpenStreetMap contributors',
             subdomains: 'abc', maxZoom: 19, opacity: 0.8}
        );

        // Remove any existing base tile layers that Folium added
        MapApp.map.eachLayer(layer => {
            if (layer._url !== undefined) {
                MapApp.map.removeLayer(layer);
            }
        });

        // Add OpenStreetMap as default
        MapApp.baseLayers['OpenStreetMap'].addTo(MapApp.map);
        MapApp.currentBaseLayer = 'OpenStreetMap';

        createControlPanel();
        createSearchDialog();
        createOpacityControl();
        createScaleDisplay();
        createMousePositionDisplay();
        addTooltipStyles();
    }

    function createControlPanel() {
        const panel = document.createElement('div');
        panel.id = 'control-panel';
        panel.innerHTML = `
            <style>
                #control-panel {
                    position: absolute;
                    top: 10px;
                    right: 10px;
                    background: white;
                    padding: 15px;
                    border-radius: 8px;
                    box-shadow: 0 2px 10px rgba(0,0,0,0.2);
                    z-index: 1000;
                    max-height: 80vh;
                    overflow-y: auto;
                    font-family: Arial, sans-serif;
                    font-size: 13px;
                    min-width: 200px;
                }
                #control-panel h3 {
                    margin: 0 0 10px 0;
                    padding-bottom: 8px;
                    border-bottom: 1px solid #ddd;
                    font-size: 14px;
                }
                #control-panel h4 {
                    margin: 15px 0 8px 0;
                    font-size: 12px;
                    color: #666;
                    text-transform: uppercase;
                }
                .region-item, .overlay-item {
                    display: flex;
                    align-items: center;
                    margin: 5px 0;
                }
                .region-item label, .overlay-item label {
                    margin-left: 8px;
                    cursor: pointer;
                    flex: 1;
                }
                .region-item .loading {
                    width: 14px;
                    height: 14px;
                    border: 2px solid #ddd;
                    border-top-color: #007bff;
                    border-radius: 50%;
                    animation: spin 1s linear infinite;
                    margin-left: 8px;
                }
                @keyframes spin {
                    to { transform: rotate(360deg); }
                }
                .search-btn {
                    display: block;
                    width: 100%;
                    padding: 8px;
                    margin-top: 15px;
                    background: #007bff;
                    color: white;
                    border: none;
                    border-radius: 4px;
                    cursor: pointer;
                }
                .search-btn:hover {
                    background: #0056b3;
                }
                #selection-info {
                    margin-top: 15px;
                    padding-top: 10px;
                    border-top: 1px solid #ddd;
                    display: none;
                }
                #selection-info.visible {
                    display: block;
                }
                .basemap-item {
                    display: flex;
                    align-items: center;
                    margin: 3px 0;
                }
                .basemap-item input {
                    margin-right: 8px;
                }
                .basemap-item label {
                    cursor: pointer;
                }
                #control-title {
                    display: flex;
                    align-items: center;
                    justify-content: space-between;
                    gap: 10px;
                    cursor: pointer;
                    user-select: none;
                }
                #control-title.collapsed {
                    margin-bottom: 0;
                    padding-bottom: 0;
                    border-bottom: none;
                }
                #control-toggle {
                    flex: 0 0 auto;
                    width: 22px;
                    height: 22px;
                    line-height: 1;
                    padding: 0;
                    font-size: 15px;
                    border: none;
                    border-radius: 4px;
                    background: #eee;
                    color: #333;
                    cursor: pointer;
                }
                #control-toggle:hover { background: #ddd; }
            </style>
            <h3 id="control-title"><span>${MapApp.manifest.name}</span><button id="control-toggle" title="Show/hide controls" aria-label="Show/hide controls">&minus;</button></h3>
            <div id="control-body">
            <h4>Base Map</h4>
            <div id="basemap-list">
                <div class="basemap-item">
                    <input type="radio" name="basemap" id="basemap-osm" value="OpenStreetMap" checked>
                    <label for="basemap-osm">OpenStreetMap</label>
                </div>
                <div class="basemap-item">
                    <input type="radio" name="basemap" id="basemap-satellite" value="Satellite">
                    <label for="basemap-satellite">Satellite</label>
                </div>
                <div class="basemap-item">
                    <input type="radio" name="basemap" id="basemap-none" value="None">
                    <label for="basemap-none">None</label>
                </div>
                <div class="basemap-item" style="margin-top:6px;border-top:1px solid #eee;padding-top:6px;">
                    <input type="checkbox" id="basemap-orm">
                    <label for="basemap-orm">OpenRailwayMap overlay</label>
                </div>
            </div>
            <h4>Regions</h4>
            <div id="region-list"></div>
            <h4>Overlays</h4>
            <div id="overlay-list"></div>
            <h4>Local Filter</h4>
            <select id="local-symbol-select" style="width:100%;padding:6px;margin-bottom:10px;border:1px solid #ddd;border-radius:4px;">
                <option value="">-- All Industries --</option>
            </select>
            <button class="search-btn" onclick="MapApp.openSearch()">Search</button>
            <div id="selection-info">
                <strong>Selected:</strong> <span id="selection-count">0</span> sections<br>
                <strong>Accumulated:</strong> <span id="selection-length">0</span> ft<br>
                <span id="gradient-row" style="display:none"><strong>Avg Grade:</strong> <span id="selection-gradient"></span><br></span>
                <button onclick="MapApp.clearSelection()" style="margin-top:5px;padding:4px 8px;font-size:12px;">Clear Selection</button>
            </div>
            </div>
        `;
        document.body.appendChild(panel);

        // Collapse/expand the control panel, leaving just the title bar. Remembered
        // per-viewer in localStorage (best-effort; ignore storage errors).
        (function () {
            const title = document.getElementById('control-title');
            const body = document.getElementById('control-body');
            const btn = document.getElementById('control-toggle');
            function apply(collapsed) {
                body.style.display = collapsed ? 'none' : '';
                title.classList.toggle('collapsed', collapsed);
                btn.innerHTML = collapsed ? '&plus;' : '&minus;';
                try { localStorage.setItem('run8_panel_collapsed', collapsed ? '1' : '0'); } catch (e) {}
            }
            let collapsed = false;
            try { collapsed = localStorage.getItem('run8_panel_collapsed') === '1'; } catch (e) {}
            apply(collapsed);
            title.addEventListener('click', function () { collapsed = !collapsed; apply(collapsed); });
        })();

        // Setup base map radio buttons
        document.querySelectorAll('input[name="basemap"]').forEach(radio => {
            radio.addEventListener('change', (e) => {
                const selectedName = e.target.value;

                // Remove current base layer
                if (MapApp.baseLayers[MapApp.currentBaseLayer]) {
                    MapApp.map.removeLayer(MapApp.baseLayers[MapApp.currentBaseLayer]);
                }

                // Add selected base layer
                if (MapApp.baseLayers[selectedName]) {
                    MapApp.baseLayers[selectedName].addTo(MapApp.map);
                    // Apply current opacity
                    const opacity = parseFloat(document.getElementById('opacity-slider').value) / 100;
                    MapApp.baseLayers[selectedName].setOpacity(opacity);
                }

                MapApp.currentBaseLayer = selectedName;
            });
        });

        // OpenRailwayMap overlay toggle
        const ormToggle = document.getElementById('basemap-orm');
        if (ormToggle) {
            ormToggle.addEventListener('change', (e) => {
                if (e.target.checked) {
                    MapApp.railOverlay.addTo(MapApp.map);
                } else {
                    MapApp.map.removeLayer(MapApp.railOverlay);
                }
            });
        }

        // Build region checkboxes
        const regionList = document.getElementById('region-list');
        for (const region of MapApp.manifest.regions) {
            const item = document.createElement('div');
            item.className = 'region-item';
            item.innerHTML = `
                <input type="checkbox" id="region-${region.id}" ${region.enabled_by_default ? 'checked' : ''}>
                <label for="region-${region.id}">${region.display_name}</label>
            `;
            regionList.appendChild(item);

            const checkbox = item.querySelector('input');
            MapApp.regionCheckboxes.set(region.id, checkbox);
            checkbox.addEventListener('change', () => toggleRegion(region.id, checkbox.checked));
        }

        // Build overlay checkboxes
        const overlayList = document.getElementById('overlay-list');
        const overlays = [
            {id: 'aiLocations', label: 'AI Spawns'},
            {id: 'industries', label: 'Industries'},
            {id: 'signals', label: 'Signals'},
            {id: 'tileBoundaries', label: 'Tile Boundaries'},
            {id: 'trains', label: 'Trains'}
        ];
        for (const overlay of overlays) {
            const item = document.createElement('div');
            item.className = 'overlay-item';
            const checked = MapApp.overlayStates[overlay.id] ? 'checked' : '';
            item.innerHTML = `
                <input type="checkbox" id="overlay-${overlay.id}" ${checked}>
                <label for="overlay-${overlay.id}">${overlay.label}</label>
            `;
            overlayList.appendChild(item);

            const checkbox = item.querySelector('input');
            checkbox.addEventListener('change', () => toggleOverlay(overlay.id, checkbox.checked));
        }

        // Setup local symbol filter dropdown
        document.getElementById('local-symbol-select').addEventListener('change', (e) => {
            MapApp.currentLocalFilter = e.target.value || null;
            applyLocalSymbolHighlighting();
        });

        // Re-weight rail-vehicle bodies so they stay wider than the track at any zoom.
        MapApp.map.on('zoomend', updateTrainWidths);
        // Show/hide per-RV destination labels by zoom (visible at ~20 m scale or tighter).
        MapApp.map.on('zoomend', updateTrainLabelVisibility);
    }

    function createSearchDialog() {
        const dialog = document.createElement('div');
        dialog.id = 'search-dialog';
        dialog.innerHTML = `
            <style>
                #search-dialog {
                    display: none;
                    position: fixed;
                    top: 50%;
                    left: 50%;
                    transform: translate(-50%, -50%);
                    background: white;
                    padding: 20px;
                    border-radius: 8px;
                    box-shadow: 0 4px 20px rgba(0,0,0,0.3);
                    z-index: 2000;
                    min-width: 350px;
                    max-width: 500px;
                    max-height: 80vh;
                    overflow-y: auto;
                }
                #search-dialog.visible {
                    display: block;
                }
                #search-dialog h3 {
                    margin: 0 0 15px 0;
                }
                #search-dialog input[type="text"] {
                    width: 100%;
                    padding: 10px;
                    border: 1px solid #ddd;
                    border-radius: 4px;
                    font-size: 14px;
                    box-sizing: border-box;
                }
                #search-dialog select {
                    width: 100%;
                    padding: 8px;
                    margin: 10px 0;
                    border: 1px solid #ddd;
                    border-radius: 4px;
                }
                #search-results {
                    margin-top: 15px;
                    max-height: 300px;
                    overflow-y: auto;
                }
                .search-result {
                    padding: 8px;
                    border-bottom: 1px solid #eee;
                    cursor: pointer;
                }
                .search-result:hover {
                    background: #f5f5f5;
                }
                .search-close {
                    position: absolute;
                    top: 10px;
                    right: 15px;
                    background: none;
                    border: none;
                    font-size: 20px;
                    cursor: pointer;
                    color: #666;
                }
                #search-overlay {
                    display: none;
                    position: fixed;
                    top: 0;
                    left: 0;
                    right: 0;
                    bottom: 0;
                    background: rgba(0,0,0,0.5);
                    z-index: 1999;
                }
                #search-overlay.visible {
                    display: block;
                }
            </style>
            <button class="search-close" id="search-close-btn">&times;</button>
            <h3>Search</h3>
            <select id="search-type">
                <option value="aiLocation">AI Location</option>
                <option value="industry">Industry</option>
                <option value="signal">Signal</option>
                <option value="section">Track Section</option>
                <option value="train">Train / Rail Vehicle</option>
            </select>
            <input type="text" id="search-input" placeholder="Enter search term...">
            <div id="search-results"></div>
        `;
        document.body.appendChild(dialog);

        const overlay = document.createElement('div');
        overlay.id = 'search-overlay';
        overlay.addEventListener('click', () => closeSearch());
        document.body.appendChild(overlay);

        const closeBtn = document.getElementById('search-close-btn');
        closeBtn.addEventListener('click', () => closeSearch());

        const input = document.getElementById('search-input');
        input.addEventListener('input', debounce(performSearch, 300));
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') closeSearch();
        });

        MapApp.searchDialog = dialog;
    }

    function createOpacityControl() {
        const control = document.createElement('div');
        control.id = 'opacity-control';
        // Initial base-map opacity from the config ([visualization] initial_map_opacity).
        const mapOpacity = (MapApp.manifest && MapApp.manifest.initial_map_opacity != null)
            ? MapApp.manifest.initial_map_opacity : 0.2;
        control.innerHTML = `
            <style>
                #opacity-control {
                    position: absolute;
                    bottom: 60px;
                    left: 10px;
                    background: white;
                    padding: 10px;
                    border-radius: 4px;
                    box-shadow: 0 2px 6px rgba(0,0,0,0.2);
                    z-index: 1000;
                    font-size: 12px;
                }
                #opacity-control input {
                    width: 100px;
                }
            </style>
            <label>Map Opacity: <input type="range" id="opacity-slider" min="0" max="100" value="${Math.round(mapOpacity * 100)}"></label>
        `;
        document.body.appendChild(control);

        // Apply the configured initial opacity to the current base layer.
        if (MapApp.baseLayers[MapApp.currentBaseLayer]) {
            MapApp.baseLayers[MapApp.currentBaseLayer].setOpacity(mapOpacity);
        }

        document.getElementById('opacity-slider').addEventListener('input', (e) => {
            const opacity = e.target.value / 100;
            // Only adjust opacity on the current base layer
            if (MapApp.baseLayers[MapApp.currentBaseLayer]) {
                MapApp.baseLayers[MapApp.currentBaseLayer].setOpacity(opacity);
            }
        });
    }

    function createMousePositionDisplay() {
        const display = document.createElement('div');
        display.id = 'mouse-position';
        display.innerHTML = `
            <style>
                #mouse-position {
                    position: absolute;
                    bottom: 10px;
                    right: 10px;
                    background: white;
                    padding: 5px 10px;
                    border-radius: 4px;
                    box-shadow: 0 2px 6px rgba(0,0,0,0.2);
                    z-index: 1000;
                    font-size: 12px;
                    font-family: monospace;
                    text-align: right;
                }
                #train-count {
                    display: none;
                    margin-bottom: 3px;
                    padding-bottom: 3px;
                    border-bottom: 1px solid #eee;
                    color: #333;
                }
                #mouse-coords { display: block; }
            </style>
            <span id="train-count"></span>
            <span id="mouse-coords">---, ---</span>
        `;
        document.body.appendChild(display);

        MapApp.map.on('mousemove', (e) => {
            const lat = e.latlng.lat.toFixed(6);
            const lon = e.latlng.lng.toFixed(6);
            document.getElementById('mouse-coords').textContent = `${lat}, ${lon}`;
        });

        MapApp.map.on('mouseout', () => {
            document.getElementById('mouse-coords').textContent = '---, ---';
        });
    }

    function createScaleDisplay() {
        L.control.scale({imperial: true, metric: true}).addTo(MapApp.map);
    }

    function addTooltipStyles() {
        const style = document.createElement('style');
        style.textContent = `
            .leaflet-tooltip {
                font-size: 14px !important;
            }
        `;
        document.head.appendChild(style);
    }

    // ========================================
    // Region Loading/Unloading
    // ========================================
    async function loadDefaultRegions() {
        const defaultRegions = MapApp.manifest.regions.filter(r => r.enabled_by_default);
        await Promise.all(defaultRegions.map(r => loadRegion(r.id)));
    }

    async function loadRegion(regionId) {
        if (MapApp.loadedRegions.has(regionId)) {
            // Already loaded, just show it
            showRegion(regionId);
            return;
        }

        const checkbox = MapApp.regionCheckboxes.get(regionId);
        const item = checkbox.closest('.region-item');

        // Show loading indicator
        const loader = document.createElement('div');
        loader.className = 'loading';
        item.appendChild(loader);
        checkbox.disabled = true;

        try {
            const response = await fetch(`data/${regionId}.json`);
            const data = await response.json();

            console.log(`Loaded region ${regionId}: ${data.sections.length} sections`);

            // Create layer groups
            const layers = {
                sections: L.layerGroup(),
                signals: L.layerGroup(),
                industries: L.layerGroup(),
                aiLocations: L.layerGroup(),
                tileBoundaries: L.layerGroup(),
                trains: L.layerGroup(),
                trainLabels: L.layerGroup()   // per-RV destination tags (shown only when zoomed in)
            };

            // Get region-specific track color (from manifest) or fall back to global default
            const regionManifest = MapApp.manifest.regions.find(r => r.id === regionId);
            const regionTrackColor = regionManifest?.track_color || COLORS.track;

            // Render sections
            for (const section of data.sections) {
                // Each section may have multiple paths (especially switches)
                const sectionGroup = L.featureGroup();

                for (const path of section.paths) {
                    const trackColor = section.is_switch ? COLORS.switch : regionTrackColor;
                    const polyline = L.polyline(path, {
                        color: trackColor,
                        weight: 5,
                        opacity: 0.8
                    });

                    // Build detailed section popup
                    const sectionType = section.is_switch ? ' (Switch)' : '';
                    let sectionPopup = `<b>Section ${section.id}${sectionType}</b><br>`;
                    sectionPopup += `Length: ${section.length_ft.toFixed(1)} ft (${section.length_m.toFixed(1)} m)<br>`;
                    sectionPopup += `Paths: ${section.paths.length}`;

                    polyline.bindPopup(sectionPopup, {maxWidth: 250});
                    polyline.bindTooltip(`Section ${section.id}`, {sticky: true});

                    // Hover highlight handlers
                    polyline.on('mouseover', function() {
                        // Don't change if section is selected
                        if (!MapApp.selectedSections.has(section.id)) {
                            this.setStyle({ color: COLORS.trackHover });
                        }
                    });
                    polyline.on('mouseout', function() {
                        // Restore original color if not selected
                        if (!MapApp.selectedSections.has(section.id)) {
                            // Determine correct color based on overlay state
                            let restoreColor = trackColor;
                            if (MapApp.overlayStates.industries && MapApp.industrySectionIds.has(`${regionId}_${section.id}`)) {
                                restoreColor = COLORS.industryTrack;
                            }
                            this.setStyle({ color: restoreColor });
                        }
                    });

                    // Click handler for selection and industry popups
                    polyline.on('click', (e) => {
                        if (e.originalEvent.shiftKey) {
                            // Shift+click for multi-section selection
                            e.originalEvent.stopPropagation();
                            toggleSectionSelection(regionId, section.id, sectionGroup, section);
                        } else if (e.originalEvent.ctrlKey) {
                            // Ctrl+click for detailed section info popup
                            e.originalEvent.stopPropagation();
                            showDetailedSectionPopup(section, e.latlng);
                        } else if (MapApp.overlayStates.industries && MapApp.industrySectionIds.has(`${regionId}_${section.id}`)) {
                            // Industry overlay active and this is an industry track - show industry popup
                            e.originalEvent.stopPropagation();
                            const industries = getIndustriesForSection(regionId, section.id);
                            if (industries.length > 0) {
                                let popupContent = '';
                                for (const ind of industries) {
                                    if (popupContent) popupContent += '<hr>';
                                    popupContent += `<b>${ind.name}</b><br>`;
                                    popupContent += `Tag: ${ind.tag}<br>`;
                                    if (ind.track_sections && ind.track_sections.length > 0) {
                                        popupContent += `Track Sections: ${ind.track_sections.join(', ')}`;
                                    }
                                }
                                L.popup({maxWidth: 300})
                                    .setLatLng(e.latlng)
                                    .setContent(popupContent)
                                    .openOn(MapApp.map);
                            }
                        }
                        // Else: normal click - let default popup show (track info)
                    });

                    sectionGroup.addLayer(polyline);
                }

                layers.sections.addLayer(sectionGroup);
                const originalColor = section.is_switch ? COLORS.switch : regionTrackColor;
                MapApp.sectionIndex.set(section.id, {region_id: regionId, polyline: sectionGroup, metadata: section, originalColor: originalColor});
            }

            // Render signals as directional triangles
            for (const signal of data.signals) {
                const fillColor = signal.type === 'absolute' ? COLORS.signalAbsolute : COLORS.signalIntermediate;
                // Border color based on head count (single vs stacked)
                const isStacked = signal.stacked_ids && signal.stacked_ids.length > 1;
                const borderColor = isStacked ? COLORS.signalBorderStacked : COLORS.signalBorderSingle;

                // Create triangle pointing in signal direction
                // rotation is in degrees, convert to radians and flip 180 degrees
                const rotRad = (signal.rotation * Math.PI / 180) + Math.PI;
                const size = 0.00023;  // Triangle size in degrees (approx 15m)

                // Triangle vertices: tip points in rotation direction
                const tip = [
                    signal.lat + size * Math.cos(rotRad),
                    signal.lon + size * Math.sin(rotRad) / Math.cos(signal.lat * Math.PI / 180)
                ];
                const baseAngle1 = rotRad + 2.5;  // ~143 degrees back
                const baseAngle2 = rotRad - 2.5;  // ~143 degrees back
                const baseSize = size * 0.6;
                const base1 = [
                    signal.lat + baseSize * Math.cos(baseAngle1),
                    signal.lon + baseSize * Math.sin(baseAngle1) / Math.cos(signal.lat * Math.PI / 180)
                ];
                const base2 = [
                    signal.lat + baseSize * Math.cos(baseAngle2),
                    signal.lon + baseSize * Math.sin(baseAngle2) / Math.cos(signal.lat * Math.PI / 180)
                ];

                const marker = L.polygon([tip, base1, base2], {
                    fillColor: fillColor,
                    color: borderColor,
                    weight: 2,
                    fillOpacity: 0.9
                });

                // Build detailed signal popup
                let signalPopup = `<b>${signal.name}</b><br>`;
                signalPopup += `Model: ${signal.model_name}<br>`;
                signalPopup += `Type: ${signal.type === 'absolute' ? 'Absolute' : 'Intermediate'}<br>`;
                signalPopup += `Dwarf: ${signal.is_dwarf ? 'Yes' : 'No'}<br>`;
                signalPopup += `Switch Indicator: ${signal.is_switch_indicator ? 'Yes' : 'No'}<br>`;
                signalPopup += `Advance Diverging: ${signal.is_advance_diverging ? 'Yes' : 'No'}`;

                marker.bindPopup(signalPopup, {maxWidth: 300});
                // Show all stacked signal IDs in tooltip for multi-head signals
                const tooltipText = isStacked
                    ? `Signals ${signal.stacked_ids.join(', ')}`
                    : signal.name;
                marker.bindTooltip(tooltipText, {sticky: true});

                layers.signals.addLayer(marker);
                MapApp.signalIndex.set(signal.id, {region_id: regionId, marker, metadata: signal});
            }

            // Render industries and track industry section IDs
            for (let i = 0; i < data.industries.length; i++) {
                const industry = data.industries[i];
                const marker = L.marker([industry.lat, industry.lon], {
                    icon: createIndustryIcon(industry.tag, true, false)
                });

                // Build detailed industry popup
                let industryPopup = `<b>${industry.name}</b><br>`;
                industryPopup += `Tag: ${industry.tag}<br>`;
                industryPopup += `Local: ${industry.local_name || 'N/A'}<br>`;
                if (industry.track_sections && industry.track_sections.length > 0) {
                    industryPopup += `Track Sections: ${industry.track_sections.join(', ')}`;
                }

                marker.bindPopup(industryPopup, {maxWidth: 300});
                layers.industries.addLayer(marker);
                MapApp.industryIndex.push({region_id: regionId, data: industry});

                // Track marker for highlighting - use index to ensure unique keys
                const compositeKey = `${regionId}_${i}`;
                MapApp.industryMarkers.set(compositeKey, {
                    marker: marker,
                    data: industry,
                    regionId: regionId
                });

                // Track unique local symbols for the filter dropdown
                if (industry.local_name) {
                    MapApp.localSymbolIndex.add(industry.local_name);
                }

                // Track which sections are industry tracks
                if (industry.track_sections) {
                    for (const sectionId of industry.track_sections) {
                        MapApp.industrySectionIds.add(`${regionId}_${sectionId}`);
                    }
                }
            }

            // Update local symbol dropdown after loading industries
            updateLocalSymbolDropdown();

            // Render AI locations
            for (const loc of data.ai_locations) {
                const marker = L.circleMarker([loc.lat, loc.lon], {
                    radius: 5,
                    fillColor: '#9900cc',
                    color: '#000',
                    weight: 1,
                    fillOpacity: 0.8
                });

                // Build detailed AI location popup
                let aiPopup = `<b>${loc.name}</b><br>`;
                aiPopup += `Type: ${loc.type_name}<br>`;
                aiPopup += `Type ID: ${loc.type_id}`;

                marker.bindPopup(aiPopup, {maxWidth: 250});
                marker.bindTooltip(loc.name, {sticky: true});

                layers.aiLocations.addLayer(marker);
                MapApp.aiLocationIndex.push({region_id: regionId, data: loc});
            }

            // Render tile boundaries
            for (const tile of data.tiles) {
                const tileColor = tile.is_corrected ? 'red' : 'black';
                const tileStatus = tile.is_corrected ? ' (CORRECTED)' : '';
                const tileCoords = `(${tile.x}, ${tile.z})`;

                // Draw tile boundary rectangle (non-interactive - only shows border)
                const rect = L.rectangle(
                    [[tile.lat_south, tile.lon_west], [tile.lat_north, tile.lon_east]],
                    {
                        color: tileColor,
                        fill: false,
                        weight: 2,
                        opacity: 0.7,
                        interactive: false  // Don't capture mouse events - let underlying elements be clickable
                    }
                );
                layers.tileBoundaries.addLayer(rect);

                // Draw center dot
                const centerLat = (tile.lat_north + tile.lat_south) / 2;
                const centerLon = (tile.lon_east + tile.lon_west) / 2;
                const dotColor = tile.is_corrected ? 'red' : 'darkgrey';
                const dotFill = tile.is_corrected ? 'red' : 'white';

                const dot = L.circleMarker([centerLat, centerLon], {
                    radius: 3,
                    color: dotColor,
                    fillColor: dotFill,
                    fillOpacity: 1.0,
                    weight: 1
                });
                dot.bindPopup(`<b>Tile ${tileCoords}${tileStatus}</b>`);
                dot.bindTooltip(`Tile ${tileCoords}${tileStatus}`, {sticky: true});
                layers.tileBoundaries.addLayer(dot);
            }

            // Render trains / rail vehicles (from an optional world save)
            renderTrains(regionId, data, layers);

            // Store region data
            MapApp.loadedRegions.set(regionId, {data, layers, visible: true});

            // Add layers to map based on overlay states
            layers.sections.addTo(MapApp.map);
            if (MapApp.overlayStates.signals) layers.signals.addTo(MapApp.map);
            if (MapApp.overlayStates.industries) {
                layers.industries.addTo(MapApp.map);
                updateIndustryTrackColors(true);  // Color industry tracks green
            }
            if (MapApp.overlayStates.aiLocations) layers.aiLocations.addTo(MapApp.map);
            if (MapApp.overlayStates.tileBoundaries) layers.tileBoundaries.addTo(MapApp.map);
            if (MapApp.overlayStates.trains) layers.trains.addTo(MapApp.map);
            updateTrainLabelVisibility();

        } catch (error) {
            console.error(`Failed to load region ${regionId}:`, error);
            checkbox.checked = false;
        } finally {
            loader.remove();
            checkbox.disabled = false;
        }
    }

    // ---- Trains / rail vehicles (from an optional world save) ----
    // Body colour: locomotives are coloured by owning railroad from the config's
    // [loco_company_colors] (keyed by the DB's INITIAL reporting mark), falling
    // back to [colors] train_loco; other cars use the per-type colour from
    // [car_type_colors] (keyed by INDUSTRY_CONFIG_CAR_TYPE), falling back to
    // [colors] train.
    function carTypeColor(t) {
        const m = (MapApp.manifest && MapApp.manifest.car_type_colors) || {};
        return t ? m[String(t).toLowerCase()] : null;
    }
    function locoCompanyColor(c) {
        const m = (MapApp.manifest && MapApp.manifest.loco_company_colors) || {};
        return c ? m[String(c).toLowerCase()] : null;
    }
    function rvBodyColor(v, isLoco) {
        if (isLoco) return locoCompanyColor(v.company) || COLORS.trainLoco;
        return carTypeColor(v.car_type) || COLORS.train;
    }

    // RV body line width in px: a real-world car width (carM, metres) converted at
    // the current zoom, floored at TRAIN_STYLE.car and capped so it can't explode.
    // Because it scales with zoom like the map/raster rail does, the RV stays wider
    // than the track at every zoom (the fixed-px vector track is 5 px). carM = 0
    // reverts to a plain fixed px width.
    const TRAIN_CAR_WIDTH_MAX_PX = 64;
    function rvBodyWeightPx() {
        const floor = TRAIN_STYLE.car || 7;
        const carM = TRAIN_STYLE.carM || 0;
        if (!carM || !MapApp.map) return floor;
        const lat = MapApp.map.getCenter().lat;
        const mPerPx = 156543.03392 * Math.cos(lat * Math.PI / 180) / Math.pow(2, MapApp.map.getZoom());
        return Math.max(floor, Math.min(TRAIN_CAR_WIDTH_MAX_PX, carM / mPerPx));
    }
    // Re-weight every rendered RV body for the current zoom (called on zoomend).
    function updateTrainWidths() {
        const w = rvBodyWeightPx();
        MapApp.loadedRegions.forEach(region => {
            if (region.layers && region.layers.trains)
                region.layers.trains.eachLayer(l => {
                    if (l._rvBody && l.setStyle) l.setStyle({ weight: w });
                });
        });
    }

    // Centered destination-tag label for one RV (shown only when zoomed in).
    function trainDestLabelIcon(text) {
        return L.divIcon({
            className: 'rv-dest-label',
            html: `<div style="transform:translate(-50%,-50%);color:#fff;`
                + `font:bold 11px/1 system-ui,sans-serif;white-space:nowrap;`
                + `text-shadow:-1px -1px 0 #000,1px -1px 0 #000,-1px 1px 0 #000,1px 1px 0 #000;">`
                + `${text}</div>`,
            iconSize: null, iconAnchor: [0, 0]
        });
    }

    // Draw each rail vehicle as a body polyline spanning its two trucks. Body
    // points are already [lat, lon] at render time (geographic mode stores them
    // that way; align mode converts them in transformData), so no coord swap.
    // `layers` carries .trains (bodies + spine) and .trainLabels (destination tags).
    function renderTrains(regionId, data, layers) {
        // Idempotent per region: drop prior entries (e.g. a re-align rebuild).
        MapApp.trainIndex = MapApp.trainIndex.filter(it => it.regionId !== regionId);
        for (const train of (data.trains || [])) {
            const carBodies = [];  // resolved bodies, for the connecting spine
            for (const v of train.vehicles) {
                if (!v.resolved || !v.body || v.body.length < 2) continue;
                const isLoco = /DieselEngine|Electric|Steam|Engine/i.test(v.unit_type || '');
                const line = L.polyline(v.body, {
                    color: rvBodyColor(v, isLoco),
                    weight: rvBodyWeightPx(),
                    opacity: 0.95,
                    // Locos get rounded end-caps (a pill shape) so they read as
                    // the powered unit without relying on colour; cars stay blunt.
                    lineCap: isLoco ? 'round' : 'butt'
                });
                line._rvBody = true;   // marks it for zoom re-weighting
                line.bindTooltip(trainVehicleTooltip(train, v), {sticky: true});
                line.bindPopup(trainVehiclePopup(train, v), {maxWidth: 300});
                line.addTo(layers.trains);
                MapApp.trainLayers.push(line);
                MapApp.trainIndex.push({regionId, trainId: train.train_id, vehicle: v, layer: line});
                carBodies.push(v.body);

                // Destination tag centered on the car (zoom-gated visibility).
                if (v.destination_tag) {
                    L.marker(_midpoint(v.body), {
                        icon: trainDestLabelIcon(v.destination_tag),
                        interactive: false, keyboard: false
                    }).addTo(layers.trainLabels);
                }
            }
            // A thin line joins all the cars in this train so a consist reads as one unit.
            drawTrainOutline(carBodies, layers.trains);
        }
    }

    // meters per screen pixel at the current view (for zoom-gated RV labels).
    function _metersPerPixel() {
        return 156543.03392 * Math.cos(MapApp.map.getCenter().lat * Math.PI / 180)
            / Math.pow(2, MapApp.map.getZoom());
    }
    // The scale bar's reading in metres (mirrors Leaflet L.control.scale: the
    // 1/2/3/5 x 10^n round number of metres a 100 px bar spans).
    function _scaleBarMeters() {
        const m = _metersPerPixel() * 100;
        if (!(m > 0)) return Infinity;
        const pow10 = Math.pow(10, Math.floor(Math.log10(m)));
        let d = m / pow10;
        d = d >= 10 ? 10 : d >= 5 ? 5 : d >= 3 ? 3 : d >= 2 ? 2 : 1;
        return pow10 * d;
    }
    // RV destination labels appear when the scale bar reads TRAIN_STYLE.labelScaleM
    // metres or tighter ([trains] label_scale_m, default 30) and Trains is on.
    function updateTrainLabelVisibility() {
        const show = MapApp.overlayStates.trains
            && _scaleBarMeters() <= (TRAIN_STYLE.labelScaleM || 30);
        MapApp.loadedRegions.forEach(region => {
            if (!region.visible || !region.layers || !region.layers.trainLabels) return;
            if (show) region.layers.trainLabels.addTo(MapApp.map);
            else MapApp.map.removeLayer(region.layers.trainLabels);
        });
        updateTrainCount();
    }

    // Lower-right status (above the coordinates): whole-world-save totals, which
    // are region-independent (they do NOT depend on which regions are enabled).
    // Live totals from serve.py override the baked manifest totals when present.
    // Shown only while the Trains overlay is on and the save has trains.
    function updateTrainCount() {
        const el = document.getElementById('train-count');
        if (!el) return;
        const wt = MapApp._liveTotals || (MapApp.manifest && MapApp.manifest.world_totals);
        if (!MapApp.overlayStates.trains || !wt) { el.style.display = 'none'; return; }
        el.textContent = `Trains: ${wt.trains}  ·  Rail vehicles: ${wt.vehicles}`;
        el.style.display = 'block';
    }

    // ---- Train outline: a thin spine connecting all cars in a train ----
    function _midpoint(body) {
        const i = body.length >> 1;
        if (body.length % 2) return body[i];
        return [(body[i-1][0]+body[i][0])/2, (body[i-1][1]+body[i][1])/2];
    }
    function _distLL(a, b) { const dy=a[0]-b[0], dx=a[1]-b[1]; return Math.hypot(dx, dy); }

    // Order the cars into a physical chain by nearest-neighbour from one end, so
    // the spine follows the train even across sections / mis-ordered XML.
    function _chainOrder(centers) {
        const n = centers.length;
        if (n <= 2) return centers.map((_, i) => i);
        // start from the centre farthest from the centroid (a train end)
        const cx = centers.reduce((s,c)=>s+c[0],0)/n, cy = centers.reduce((s,c)=>s+c[1],0)/n;
        let start = 0, best = -1;
        for (let i=0;i<n;i++){ const d=_distLL(centers[i],[cx,cy]); if(d>best){best=d;start=i;} }
        const used = new Array(n).fill(false);
        const order = [start]; used[start] = true;
        for (let k=1;k<n;k++){
            const last = centers[order[order.length-1]];
            let nb=-1, bd=Infinity;
            for (let i=0;i<n;i++){ if(used[i])continue; const d=_distLL(centers[i],last); if(d<bd){bd=d;nb=i;} }
            order.push(nb); used[nb]=true;
        }
        return order;
    }

    // Outer end of an end car's body (the vertex farther from `neighbour`), so
    // the spine reaches the very tips of the train rather than stopping at the
    // end-car centres.
    function _outerEnd(body, neighbour) {
        const e0 = body[0], eL = body[body.length-1];
        return (!neighbour || _distLL(e0, neighbour) >= _distLL(eL, neighbour)) ? e0 : eL;
    }

    function drawTrainOutline(carBodies, layerGroup) {
        // A single-vehicle train has no consist to connect, so draw no spine
        // (otherwise the end->centre->end line shows as a stub through the one car).
        if (carBodies.length < 2) return;
        const centers = carBodies.map(_midpoint);
        const order = _chainOrder(centers);
        const bodies = order.map(i => carBodies[i]);
        const cen = order.map(i => centers[i]);

        const frontEnd = _outerEnd(bodies[0], cen.length > 1 ? cen[1] : null);
        const rearEnd = _outerEnd(bodies[bodies.length-1], cen.length > 1 ? cen[cen.length-2] : null);

        // Thin spine: one end -> each car centre -> the other end.
        L.polyline([frontEnd, ...cen, rearEnd],
            { color: COLORS.trainOutline, weight: TRAIN_STYLE.spine, opacity: 0.9 }).addTo(layerGroup);
    }

    function trainVehicleTooltip(train, v) {
        // Fixed-label, monospace layout so the colons align.
        return `<div style="font-family:monospace;white-space:pre;margin:0">`
             + `Train  : ${train.train_id}<br>`
             + `RV num : ${v.unit_number || ''}<br>`
             + `RV tag : ${v.destination_tag || ''}<br>`
             + `RV typ : ${v.car_type || ''}</div>`;
    }

    function trainVehiclePopup(train, v) {
        let html = `<b>Train ${train.train_id}</b>${train.was_ai ? ' (AI)' : ''}<br>`;
        html += `Unit: ${v.unit_number || 'N/A'}<br>`;
        html += `Type: ${v.unit_type || 'N/A'}<br>`;
        if (v.car_type) html += `Car type: ${v.car_type}<br>`;
        html += `Destination: ${v.destination_tag || 'N/A'}`;
        if (v.rv_filename) html += `<br><span style="color:#888;font-size:11px;">${v.rv_filename}</span>`;
        return html;
    }

    function unloadRegion(regionId) {
        const region = MapApp.loadedRegions.get(regionId);
        if (!region) return;

        // Remove layers from map
        for (const layer of Object.values(region.layers)) {
            MapApp.map.removeLayer(layer);
        }

        // Remove from indexes
        for (const [sectionId, data] of MapApp.sectionIndex) {
            if (data.region_id === regionId) {
                MapApp.sectionIndex.delete(sectionId);
            }
        }
        for (const [signalId, data] of MapApp.signalIndex) {
            if (data.region_id === regionId) {
                MapApp.signalIndex.delete(signalId);
            }
        }

        // Remove industry markers for this region
        for (const [compositeKey, entry] of MapApp.industryMarkers) {
            if (entry.regionId === regionId) {
                MapApp.industryMarkers.delete(compositeKey);
            }
        }

        MapApp.industryIndex = MapApp.industryIndex.filter(i => i.region_id !== regionId);
        MapApp.aiLocationIndex = MapApp.aiLocationIndex.filter(i => i.region_id !== regionId);
        MapApp.trainIndex = MapApp.trainIndex.filter(i => i.regionId !== regionId);

        // Rebuild local symbol index from remaining industries
        MapApp.localSymbolIndex.clear();
        for (const item of MapApp.industryIndex) {
            if (item.data.local_name) {
                MapApp.localSymbolIndex.add(item.data.local_name);
            }
        }

        // Update dropdown and clear filter if no longer valid
        updateLocalSymbolDropdown();

        // Clear selection if it was in this region
        if (MapApp.currentSelectionRegion === regionId) {
            clearSelection();
        }

        region.visible = false;
    }

    function showRegion(regionId) {
        const region = MapApp.loadedRegions.get(regionId);
        if (!region || region.visible) return;

        region.layers.sections.addTo(MapApp.map);
        if (MapApp.overlayStates.signals) region.layers.signals.addTo(MapApp.map);
        if (MapApp.overlayStates.industries) region.layers.industries.addTo(MapApp.map);
        if (MapApp.overlayStates.aiLocations) region.layers.aiLocations.addTo(MapApp.map);
        if (MapApp.overlayStates.tileBoundaries) region.layers.tileBoundaries.addTo(MapApp.map);
        if (MapApp.overlayStates.trains) region.layers.trains.addTo(MapApp.map);

        region.visible = true;
        updateTrainLabelVisibility();
    }

    function toggleRegion(regionId, enabled) {
        if (enabled) {
            loadRegion(regionId);
        } else {
            unloadRegion(regionId);
        }
    }

    // ========================================
    // Overlay Toggle
    // ========================================
    function toggleOverlay(overlayId, enabled) {
        MapApp.overlayStates[overlayId] = enabled;

        for (const [regionId, region] of MapApp.loadedRegions) {
            if (!region.visible) continue;

            const layer = region.layers[overlayId];
            if (layer) {
                if (enabled) {
                    layer.addTo(MapApp.map);
                } else {
                    MapApp.map.removeLayer(layer);
                }
            }
        }

        // Trains overlay also gates the zoom-based destination labels.
        if (overlayId === 'trains') updateTrainLabelVisibility();

        // When toggling industries, recolor industry tracks and apply local filter highlighting
        if (overlayId === 'industries') {
            if (enabled && MapApp.currentLocalFilter) {
                // Apply local filter highlighting if a filter is active
                applyLocalSymbolHighlighting();
            } else {
                updateIndustryTrackColors(enabled);
            }
        }
    }

    function updateIndustryTrackColors(showIndustryColor) {
        for (const [sectionId, data] of MapApp.sectionIndex) {
            const compositeKey = `${data.region_id}_${sectionId}`;
            if (!MapApp.industrySectionIds.has(compositeKey)) continue;
            if (MapApp.selectedSections.has(sectionId)) continue;  // Don't change selected sections

            const color = showIndustryColor ? COLORS.industryTrack : data.originalColor;
            data.polyline.eachLayer(layer => {
                if (layer.setStyle) layer.setStyle({color: color});
            });
        }
    }

    function getIndustriesForSection(regionId, sectionId) {
        const industries = [];
        for (const item of MapApp.industryIndex) {
            if (item.region_id === regionId &&
                item.data.track_sections &&
                item.data.track_sections.includes(sectionId)) {
                industries.push(item.data);
            }
        }
        return industries;
    }

    // ========================================
    // Local Symbol Filtering
    // ========================================
    function createIndustryIcon(tag, isHighlighted, filterActive) {
        let style;

        if (!filterActive) {
            // No filter active - normal style
            style = `color:${COLORS.industryTrack};font-size:11px;font-weight:bold;white-space:nowrap;text-shadow:-1px -1px 0 #fff,1px -1px 0 #fff,-1px 1px 0 #fff,1px 1px 0 #fff;`;
        } else if (isHighlighted) {
            // Filter active AND this matches - highlighted style
            style = `color:#FF4500;font-size:14px;font-weight:bold;white-space:nowrap;text-shadow:-1px -1px 0 #fff,1px -1px 0 #fff,-1px 1px 0 #fff,1px 1px 0 #fff;background:rgba(255,255,0,0.3);padding:2px 4px;border-radius:3px;`;
        } else {
            // Filter active but doesn't match - dimmed style
            style = `color:#888888;font-size:10px;font-weight:normal;white-space:nowrap;text-shadow:none;opacity:0.5;`;
        }

        return L.divIcon({
            className: 'industry-marker',
            html: `<div style="${style}">${tag}</div>`,
            iconAnchor: [0, 0]
        });
    }

    function updateLocalSymbolDropdown() {
        const select = document.getElementById('local-symbol-select');
        if (!select) return;

        const currentValue = select.value;

        // Clear existing options except "All"
        while (select.options.length > 1) {
            select.remove(1);
        }

        // Get sorted list of local symbols
        const symbols = Array.from(MapApp.localSymbolIndex).sort();

        // Add options
        for (const symbol of symbols) {
            const option = document.createElement('option');
            option.value = symbol;
            option.textContent = symbol;
            select.appendChild(option);
        }

        // Restore previous selection if still valid
        if (currentValue && MapApp.localSymbolIndex.has(currentValue)) {
            select.value = currentValue;
        } else if (MapApp.currentLocalFilter && !MapApp.localSymbolIndex.has(MapApp.currentLocalFilter)) {
            // Filter is no longer valid, reset
            MapApp.currentLocalFilter = null;
            select.value = '';
        }
    }

    function applyLocalSymbolHighlighting() {
        const filterSymbol = MapApp.currentLocalFilter;
        const filterActive = filterSymbol !== null;

        // Update industry markers
        for (const [compositeKey, entry] of MapApp.industryMarkers) {
            const { marker, data, regionId } = entry;
            const isMatch = filterSymbol ? (data.local_name === filterSymbol) : true;

            // Update marker icon based on match status
            const icon = createIndustryIcon(data.tag, isMatch, filterActive);
            marker.setIcon(icon);
        }

        // Update track section colors if industries overlay is enabled
        if (MapApp.overlayStates.industries) {
            for (const [sectionId, data] of MapApp.sectionIndex) {
                const compositeKey = `${data.region_id}_${sectionId}`;
                if (!MapApp.industrySectionIds.has(compositeKey)) continue;
                if (MapApp.selectedSections.has(sectionId)) continue;

                // Find if any industry using this section matches the filter
                const industries = getIndustriesForSection(data.region_id, sectionId);
                let sectionMatches = false;
                if (filterSymbol) {
                    sectionMatches = industries.some(ind => ind.local_name === filterSymbol);
                } else {
                    sectionMatches = true;  // No filter = all match
                }

                let color;
                if (!filterActive) {
                    color = COLORS.industryTrack;
                } else if (sectionMatches) {
                    color = '#FF4500';  // Orange-red for highlighted
                } else {
                    color = '#CCCCCC';  // Gray for non-matching
                }

                data.polyline.eachLayer(layer => {
                    if (layer.setStyle) layer.setStyle({ color: color });
                });
            }
        }
    }

    // ========================================
    // Section Selection
    // ========================================
    function toggleSectionSelection(regionId, sectionId, featureGroup, metadata) {
        // Check if selecting from different region
        if (MapApp.currentSelectionRegion && MapApp.currentSelectionRegion !== regionId) {
            showMessage('Cannot select sections from multiple regions. Clear selection first.');
            return;
        }

        if (MapApp.selectedSections.has(sectionId)) {
            // Deselect - apply style to all layers in the feature group
            MapApp.selectedSections.delete(sectionId);
            featureGroup.eachLayer(layer => {
                if (layer.setStyle) layer.setStyle({color: data.originalColor || COLORS.track, weight: 3});
            });

            if (MapApp.selectedSections.size === 0) {
                MapApp.currentSelectionRegion = null;
            }
        } else {
            // Select - apply style to all layers in the feature group
            MapApp.selectedSections.set(sectionId, {polyline: featureGroup, metadata});
            MapApp.currentSelectionRegion = regionId;
            featureGroup.eachLayer(layer => {
                if (layer.setStyle) layer.setStyle({color: COLORS.trackSelected, weight: 5});
            });
        }

        updateSelectionInfo();
    }

    function clearSelection() {
        for (const [sectionId, data] of MapApp.selectedSections) {
            data.polyline.eachLayer(layer => {
                if (layer.setStyle) layer.setStyle({color: data.originalColor || COLORS.track, weight: 3});
            });
        }
        MapApp.selectedSections.clear();
        MapApp.currentSelectionRegion = null;
        updateSelectionInfo();
    }

    function updateSelectionInfo() {
        const infoDiv = document.getElementById('selection-info');
        const countSpan = document.getElementById('selection-count');
        const lengthSpan = document.getElementById('selection-length');
        const gradientRow = document.getElementById('gradient-row');
        const gradientSpan = document.getElementById('selection-gradient');

        if (MapApp.selectedSections.size === 0) {
            infoDiv.classList.remove('visible');
            if (gradientRow) gradientRow.style.display = 'none';
        } else {
            infoDiv.classList.add('visible');
            countSpan.textContent = MapApp.selectedSections.size;

            let totalLengthFt = 0;
            for (const [sectionId, data] of MapApp.selectedSections) {
                totalLengthFt += data.metadata.length_ft;
            }
            lengthSpan.textContent = totalLengthFt.toFixed(1);

            // Gradient: elevation from first-clicked section start to last-clicked section end
            const entries = Array.from(MapApp.selectedSections.values());
            if (entries.length >= 2 && gradientRow && gradientSpan) {
                const firstMeta = entries[0].metadata;
                const lastMeta = entries[entries.length - 1].metadata;
                const totalLengthM = totalLengthFt / 3.28084;
                const elevDiff = lastMeta.elevation_end_m - firstMeta.elevation_start_m;
                const gradientPct = totalLengthM > 0 ? (elevDiff / totalLengthM) * 100 : 0;
                const sign = gradientPct > 0 ? '+' : '';
                gradientSpan.textContent = `${sign}${gradientPct.toFixed(2)}%`;
                gradientRow.style.display = 'inline';
            } else if (gradientRow) {
                gradientRow.style.display = 'none';
            }
        }
    }

    // ========================================
    // Detailed Section Popup (Alt+click)
    // ========================================
    function showDetailedSectionPopup(section, latlng) {
        const sectionType = section.is_switch ? 'Switch/Turnout' : 'Track Section';
        let content = `<b>Section ${section.id}</b> (${sectionType})<br>`;
        content += `Length: ${section.length_ft.toFixed(1)} ft (${section.length_m.toFixed(1)} m)<br>`;
        content += `Track Type: ${section.track_type}<br>`;
        content += `Retarder: ${section.retarder_mph}`;
        L.popup({maxWidth: 300}).setLatLng(latlng).setContent(content).openOn(MapApp.map);
    }

    // ========================================
    // Search
    // ========================================
    function openSearch() {
        document.getElementById('search-dialog').classList.add('visible');
        document.getElementById('search-overlay').classList.add('visible');
        document.getElementById('search-input').focus();
    }

    function closeSearch() {
        document.getElementById('search-dialog').classList.remove('visible');
        document.getElementById('search-overlay').classList.remove('visible');
        document.getElementById('search-input').value = '';
        document.getElementById('search-results').innerHTML = '';
    }

    function performSearch() {
        const searchType = document.getElementById('search-type').value;
        const query = document.getElementById('search-input').value.trim().toLowerCase();
        const resultsDiv = document.getElementById('search-results');

        if (!query) {
            resultsDiv.innerHTML = '';
            return;
        }

        let results = [];

        if (searchType === 'section') {
            const queryNum = parseInt(query);
            for (const [sectionId, data] of MapApp.sectionIndex) {
                if (sectionId.toString().includes(query) || sectionId === queryNum) {
                    results.push({
                        type: 'section',
                        id: sectionId,
                        label: `Section ${sectionId}`,
                        region: data.region_id,
                        data: data
                    });
                }
            }
        } else if (searchType === 'signal') {
            const queryNum = parseInt(query);
            for (const [signalId, data] of MapApp.signalIndex) {
                if (signalId.toString().includes(query) || signalId === queryNum) {
                    results.push({
                        type: 'signal',
                        id: signalId,
                        label: `Signal ${signalId}`,
                        region: data.region_id,
                        data: data
                    });
                }
            }
        } else if (searchType === 'industry') {
            for (const item of MapApp.industryIndex) {
                if (item.data.tag.toLowerCase().includes(query) ||
                    item.data.name.toLowerCase().includes(query)) {
                    results.push({
                        type: 'industry',
                        id: item.data.tag,
                        label: `${item.data.tag} - ${item.data.name}`,
                        region: item.region_id,
                        data: item
                    });
                }
            }
        } else if (searchType === 'aiLocation') {
            for (const item of MapApp.aiLocationIndex) {
                if (item.data.name.toLowerCase().includes(query)) {
                    results.push({
                        type: 'aiLocation',
                        id: item.data.id,
                        label: `${item.data.name} (${item.data.type_name})`,
                        region: item.region_id,
                        data: item
                    });
                }
            }
        } else if (searchType === 'train') {
            // Match trainID, destinationTag, or unitNumber (substring, case-insensitive)
            for (let i = 0; i < MapApp.trainIndex.length; i++) {
                const it = MapApp.trainIndex[i];
                const v = it.vehicle;
                const hay = [String(it.trainId), v.unit_number || '', v.destination_tag || '']
                    .join(' ').toLowerCase();
                if (hay.includes(query)) {
                    results.push({
                        type: 'train',
                        id: i,
                        label: `Train ${it.trainId} · ${v.unit_number || '?'}`
                            + (v.destination_tag ? ` → ${v.destination_tag}` : ''),
                        region: it.regionId,
                        data: it
                    });
                }
            }
        }

        // Limit results
        results = results.slice(0, 50);

        resultsDiv.innerHTML = results.length === 0
            ? '<div style="padding:10px;color:#666;">No results found</div>'
            : results.map(r => {
                // Escape for HTML attribute - use single quotes and escape any single quotes in the value
                const idStr = typeof r.id === 'string'
                    ? `'${r.id.replace(/'/g, "\\'")}'`
                    : r.id;
                return `
                <div class="search-result" onclick="MapApp.goToResult('${r.type}', ${idStr}, '${r.region}')">
                    <strong>${r.label}</strong>
                    <span style="color:#666;font-size:11px;"> (${r.region})</span>
                </div>
                `;
            }).join('');
    }

    function goToResult(type, id, regionId) {
        closeSearch();

        if (type === 'section') {
            const data = MapApp.sectionIndex.get(id);
            if (data) {
                MapApp.map.fitBounds(data.polyline.getBounds(), {padding: [50, 50]});
                // Open popup on first layer in the group
                data.polyline.eachLayer(layer => {
                    if (layer.openPopup) {
                        layer.openPopup();
                        return false; // Stop after first
                    }
                });
            }
        } else if (type === 'signal') {
            const data = MapApp.signalIndex.get(id);
            if (data) {
                MapApp.map.setView([data.metadata.lat, data.metadata.lon], 16);
                data.marker.openPopup();
            }
        } else if (type === 'industry') {
            const item = MapApp.industryIndex.find(i => i.data.tag === id && i.region_id === regionId);
            if (item) {
                MapApp.map.setView([item.data.lat, item.data.lon], 16);
            }
        } else if (type === 'aiLocation') {
            const item = MapApp.aiLocationIndex.find(i => i.data.id === id && i.region_id === regionId);
            if (item) {
                MapApp.map.setView([item.data.lat, item.data.lon], 16);
            }
        } else if (type === 'train') {
            const it = MapApp.trainIndex[id];
            if (it && it.layer) {
                // Make sure the Trains overlay is visible so the hit is shown.
                if (!MapApp.overlayStates.trains) {
                    const cb = document.getElementById('overlay-trains');
                    if (cb) cb.checked = true;
                    toggleOverlay('trains', true);
                }
                MapApp.map.fitBounds(it.layer.getBounds(), {padding: [80, 80], maxZoom: 18});
                it.layer.openPopup();
            }
        }
    }

    // ========================================
    // Utilities
    // ========================================
    function debounce(func, wait) {
        let timeout;
        return function(...args) {
            clearTimeout(timeout);
            timeout = setTimeout(() => func.apply(this, args), wait);
        };
    }

    function showMessage(msg) {
        const toast = document.createElement('div');
        toast.style.cssText = 'position:fixed;bottom:20px;left:50%;transform:translateX(-50%);background:#333;color:white;padding:10px 20px;border-radius:4px;z-index:3000;';
        toast.textContent = msg;
        document.body.appendChild(toast);
        setTimeout(() => toast.remove(), 3000);
    }

    // ========================================
    // Public API
    // ========================================
    window.MapApp = {
        openSearch,
        closeSearch,
        goToResult,
        clearSelection: clearSelection
    };

    // Start initialization
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        setTimeout(init, 100);
    }
})();
</script>
'''


def generate_tile_based_html(config: VisualizationConfig) -> str:
    """Generate HTML for tile-based coordinate mode using L.CRS.Simple"""
    colors = config.colors

    return f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{config.name} - Tile-Based View</title>
    <link rel="stylesheet" href="leaflet/leaflet.css" />
    <script src="leaflet/leaflet.js"></script>
    <script>window.L||document.write('<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"><\/script>')</script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        html, body {{ height: 100%; width: 100%; }}
        #map {{ height: 100%; width: 100%; background-color: {colors.background}; }}
        .control-panel {{
            position: absolute;
            top: 10px;
            right: 10px;
            z-index: 1000;
            background: white;
            padding: 15px;
            border-radius: 5px;
            box-shadow: 0 2px 6px rgba(0,0,0,0.3);
            max-width: 280px;
            max-height: calc(100vh - 40px);
            overflow-y: auto;
            font-family: Arial, sans-serif;
            font-size: 13px;
        }}
        .control-panel h3 {{ margin-bottom: 10px; font-size: 14px; }}
        .control-panel label {{ display: block; margin: 5px 0; cursor: pointer; }}
        .control-panel input[type="checkbox"] {{ margin-right: 8px; }}
        .region-list {{ margin-bottom: 15px; padding-bottom: 10px; border-bottom: 1px solid #ddd; }}
        .overlay-toggles {{ margin-bottom: 15px; padding-bottom: 10px; border-bottom: 1px solid #ddd; }}
        #mouse-position {{
            position: absolute;
            bottom: 10px;
            right: 10px;
            z-index: 1000;
            background: rgba(255,255,255,0.9);
            padding: 5px 10px;
            border-radius: 3px;
            font-family: monospace;
            font-size: 12px;
        }}
        .search-btn {{
            position: absolute;
            top: 10px;
            left: 50px;
            z-index: 1000;
            background: white;
            border: 2px solid rgba(0,0,0,0.2);
            border-radius: 4px;
            padding: 5px 10px;
            cursor: pointer;
            font-size: 16px;
        }}
        .search-btn:hover {{ background: #f4f4f4; }}
        .search-overlay {{
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(0,0,0,0.5);
            z-index: 1999;
        }}
        .search-overlay.visible {{ display: block; }}
        .search-dialog {{
            display: none;
            position: fixed;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            z-index: 2000;
            background: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 4px 20px rgba(0,0,0,0.3);
            min-width: 350px;
            max-width: 500px;
            max-height: 80vh;
            overflow-y: auto;
        }}
        .search-dialog.visible {{ display: block; }}
        .search-dialog h3 {{ margin: 0 0 15px 0; }}
        .search-dialog select {{
            width: 100%;
            padding: 8px;
            margin-bottom: 10px;
            border: 1px solid #ddd;
            border-radius: 4px;
        }}
        .search-dialog input {{
            width: 100%;
            padding: 10px;
            border: 1px solid #ddd;
            border-radius: 4px;
            font-size: 14px;
            box-sizing: border-box;
        }}
        .search-dialog .search-close {{
            position: absolute;
            top: 10px;
            right: 15px;
            background: none;
            border: none;
            font-size: 20px;
            cursor: pointer;
            color: #666;
        }}
        .search-results {{
            margin-top: 15px;
            max-height: 300px;
            overflow-y: auto;
        }}
        .search-result {{
            padding: 8px;
            border-bottom: 1px solid #eee;
            cursor: pointer;
        }}
        .search-result:hover {{ background: #f5f5f5; }}
        .leaflet-popup-content {{ font-size: 12px; }}
        .selection-info {{
            margin-top: 10px;
            padding-top: 10px;
            border-top: 1px solid #ddd;
        }}
        .scale-control {{
            position: absolute;
            bottom: 10px;
            left: 10px;
            z-index: 1000;
            background: rgba(255,255,255,0.9);
            padding: 3px 8px;
            border-radius: 3px;
            font-family: monospace;
            font-size: 11px;
        }}
    </style>
</head>
<body>
    <div id="map"></div>

    <button class="search-btn" onclick="openSearch()">&#128269;</button>
    <div class="search-overlay" id="search-overlay" onclick="closeSearch()"></div>
    <div class="search-dialog" id="search-dialog">
        <button class="search-close" onclick="closeSearch()">&times;</button>
        <h3>Search</h3>
        <select id="search-type">
            <option value="aiLocation">AI Location</option>
            <option value="industry">Industry</option>
            <option value="section">Track Section</option>
            <option value="signal">Signal</option>
            <option value="train">Train / Rail Vehicle</option>
        </select>
        <input type="text" id="search-input" placeholder="Enter search term..." oninput="performSearch()">
        <div class="search-results" id="search-results"></div>
    </div>

    <div class="control-panel">
        <h3>Regions</h3>
        <div class="region-list" id="region-list"></div>

        <h3>Overlays</h3>
        <div class="overlay-toggles">
            <label><input type="checkbox" id="toggle-ai"> AI Locations</label>
            <label><input type="checkbox" id="toggle-industries"> Industries</label>
            <label><input type="checkbox" id="toggle-signals"> Signals</label>
            <label><input type="checkbox" id="toggle-tiles"> Tile Boundaries</label>
            <label><input type="checkbox" id="toggle-trains"> Trains</label>
            <label><input type="checkbox" id="toggle-area-labels"> Area Labels</label>
            <div style="font-size:11px;color:#666;margin-top:4px;">Shift+Click to place a label, then click along a track to set its angle (Esc = flat)</div>
        </div>

        <h3>Local Filter</h3>
        <select id="local-symbol-select" style="width:100%;padding:6px;margin-bottom:10px;border:1px solid #ddd;border-radius:4px;">
            <option value="">-- All Industries --</option>
        </select>

        <div class="selection-info" id="selection-info" style="display:none;">
            <strong>Selection</strong>
            <div id="selection-count">0 sections</div>
            <div id="selection-length">0 ft</div>
            <div id="gradient-row" style="display:none">Avg Grade: <span id="selection-gradient"></span></div>
            <button onclick="clearSelection()" style="margin-top:5px;padding:3px 8px;">Clear</button>
        </div>
    </div>

    <div id="mouse-position">X: 0.0m, Y: 0.0m</div>
    <div class="scale-control" id="scale-control">Scale: calculating...</div>

<script>
window.COLORS = {{
    track: '{colors.track}',
    trackSelected: '{colors.track_selected}',
    trackHover: '{colors.track_hover}',
    switch: '{colors.switch}',
    industryTrack: '{colors.industry_track}',
    signalAbsolute: '{colors.signal_absolute}',
    signalIntermediate: '{colors.signal_intermediate}',
    signalBorderSingle: '{colors.signal_border_single}',
    signalBorderStacked: '{colors.signal_border_stacked}',
    areaLabel: '{colors.area_label}',
    train: '{colors.train}',
    trainLoco: '{colors.train_loco}',
    trainOutline: '{colors.train_outline}'
}};
window.TRAIN_STYLE = {{car: {config.train_car_width}, spine: {config.train_spine_width}, carM: {config.train_car_width_m}, labelScaleM: {config.train_label_scale_m}}};

(function() {{
    'use strict';

    const COLORS = window.COLORS;
    const TRAIN_STYLE = window.TRAIN_STYLE || {{car: 7, spine: 1.5, carM: 3.5, labelScaleM: 30}};

    const MapApp = {{
        map: null,
        manifest: null,
        loadedRegions: new Map(),
        sectionIndex: new Map(),
        signalIndex: new Map(),
        industryIndex: [],
        aiLocationIndex: [],
        industrySectionIds: new Set(),
        selectedSections: new Map(),
        areaLabelsLayer: null,   // global L.layerGroup for user-defined area labels
        overlayStates: {{ signals: false, industries: false, aiLocations: false, tileBoundaries: false, trains: false, areaLabels: false }},
        signalLayers: [],
        industryLayers: [],
        aiLayers: [],
        tileLayers: [],
        trainLayers: [],
        trainIndex: [],   // {{ regionId, trainId, vehicle, layer }} for search
        // Local symbol filtering state
        localSymbolIndex: new Set(),
        currentLocalFilter: null,
        industryMarkers: new Map()
    }};

    // Initialize map with L.CRS.Simple for tile-based coordinates
    function init() {{
        MapApp.map = L.map('map', {{
            crs: L.CRS.Simple,
            minZoom: -10,
            maxZoom: 5,
            zoomSnap: 0.25,
            zoomDelta: 0.5
        }});

        // Set initial view (will be adjusted after loading data)
        MapApp.map.setView([0, 0], 0);

        // Mouse position display
        MapApp.map.on('mousemove', (e) => {{
            const x = e.latlng.lng.toFixed(1);
            const y = e.latlng.lat.toFixed(1);
            document.getElementById('mouse-position').textContent = `X: ${{x}}m, Y: ${{y}}m`;
        }});

        // Shift+Click to capture an area label; the next click sets the angle.
        MapApp.map.on('click', (e) => {{
            if (MapApp.areaCapture && MapApp.areaCapture.pending) {{
                finalizeAreaCapture(e.latlng);
            }} else if (e.originalEvent && e.originalEvent.shiftKey) {{
                startAreaCapture(e.latlng);
            }}
        }});

        // Update scale on zoom
        MapApp.map.on('zoomend', updateScale);
        MapApp.map.on('zoomend', updateAreaLabelSizes);

        loadManifest();
    }}

    function updateScale() {{
        // In L.CRS.Simple, 1 unit = 1 pixel at zoom 0
        // At zoom level z, 1 unit = 2^z pixels
        const zoom = MapApp.map.getZoom();
        const pixelsPerMeter = Math.pow(2, zoom);
        const metersPerPixel = 1 / pixelsPerMeter;

        // Calculate a nice scale bar length
        const containerWidth = MapApp.map.getContainer().offsetWidth;
        const targetBarPixels = 100;  // Aim for ~100px bar
        const targetMeters = targetBarPixels * metersPerPixel;

        // Round to nice numbers
        let scaleMeters;
        if (targetMeters >= 1000) scaleMeters = Math.round(targetMeters / 1000) * 1000;
        else if (targetMeters >= 100) scaleMeters = Math.round(targetMeters / 100) * 100;
        else if (targetMeters >= 10) scaleMeters = Math.round(targetMeters / 10) * 10;
        else scaleMeters = Math.round(targetMeters);

        if (scaleMeters < 1) scaleMeters = 1;

        const label = scaleMeters >= 1000 ? `${{scaleMeters/1000}} km` : `${{scaleMeters}} m`;
        document.getElementById('scale-control').textContent = `Scale: ${{label}}`;
    }}

    async function loadManifest() {{
        try {{
            const response = await fetch('manifest.json');
            MapApp.manifest = await response.json();
            setupRegionControls();
            buildAreaLabels();

            // Load enabled regions
            for (const region of MapApp.manifest.regions) {{
                if (region.enabled_by_default) {{
                    await loadRegion(region.id);
                }}
            }}

            fitBoundsToData();
            MapApp.labelBaseZoom = MapApp.map.getZoom();
            updateAreaLabelSizes();
            updateScale();
        }} catch (error) {{
            console.error('Failed to load manifest:', error);
        }}
    }}

    function setupRegionControls() {{
        const list = document.getElementById('region-list');
        list.innerHTML = '';

        for (const region of MapApp.manifest.regions) {{
            const label = document.createElement('label');
            const checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            checkbox.checked = region.enabled_by_default;
            checkbox.onchange = () => toggleRegion(region.id, checkbox.checked);
            label.appendChild(checkbox);
            label.appendChild(document.createTextNode(region.display_name));
            list.appendChild(label);
        }}

        // Overlay toggles
        document.getElementById('toggle-signals').onchange = (e) => toggleOverlay('signals', e.target.checked);
        document.getElementById('toggle-industries').onchange = (e) => toggleOverlay('industries', e.target.checked);
        document.getElementById('toggle-ai').onchange = (e) => toggleOverlay('aiLocations', e.target.checked);
        document.getElementById('toggle-tiles').onchange = (e) => toggleOverlay('tileBoundaries', e.target.checked);
        document.getElementById('toggle-trains').onchange = (e) => toggleOverlay('trains', e.target.checked);
        document.getElementById('toggle-area-labels').onchange = (e) => toggleOverlay('areaLabels', e.target.checked);

        // Local symbol filter dropdown
        document.getElementById('local-symbol-select').addEventListener('change', (e) => {{
            MapApp.currentLocalFilter = e.target.value || null;
            applyLocalSymbolHighlighting();
        }});
    }}

    async function loadRegion(regionId) {{
        if (MapApp.loadedRegions.has(regionId)) return;

        try {{
            const response = await fetch(`data/${{regionId}}.json`);
            const data = await response.json();

            const layers = {{
                tracks: L.layerGroup().addTo(MapApp.map),
                signals: L.layerGroup(),
                industries: L.layerGroup(),
                aiLocations: L.layerGroup(),
                tiles: L.layerGroup(),
                trains: L.layerGroup()
            }};

            // Build industry section set
            for (const ind of data.industries) {{
                for (const secId of ind.track_sections) {{
                    MapApp.industrySectionIds.add(`${{regionId}}_${{secId}}`);
                }}
            }}

            // Get region-specific track color (from manifest) or fall back to global default
            const regionManifest = MapApp.manifest.regions.find(r => r.id === regionId);
            const regionTrackColor = regionManifest?.track_color || COLORS.track;

            // Render tracks
            for (const section of data.sections) {{
                const isIndustry = MapApp.industrySectionIds.has(`${{regionId}}_${{section.id}}`);
                const trackColor = section.is_switch ? COLORS.switch : regionTrackColor;
                const color = section.is_switch ? COLORS.switch :
                              (MapApp.overlayStates.industries && isIndustry) ? COLORS.industryTrack : regionTrackColor;

                // Use LayerGroup to collect all polylines for this section (like geographic mode)
                const sectionGroup = L.featureGroup();

                for (const path of section.paths) {{
                    // In tile-based mode, coordinates are [x, y] (stored as [lat, lon] in data)
                    // Leaflet expects [lat, lng] = [y, x], so we need to swap
                    const coords = path.map(p => [p[1], p[0]]);  // Swap to [y, x]

                    const polyline = L.polyline(coords, {{
                        color: color,
                        weight: 5,
                        opacity: 0.8
                    }});

                    polyline.on('click', (e) => handleTrackClick(e, section, regionId, sectionGroup));
                    polyline.on('mouseover', () => {{
                        if (!MapApp.selectedSections.has(section.id)) {{
                            sectionGroup.eachLayer(layer => layer.setStyle({{ color: COLORS.trackHover }}));
                        }}
                    }});
                    polyline.on('mouseout', () => {{
                        if (!MapApp.selectedSections.has(section.id)) {{
                            const c = section.is_switch ? COLORS.switch :
                                      (MapApp.overlayStates.industries && isIndustry) ? COLORS.industryTrack : regionTrackColor;
                            sectionGroup.eachLayer(layer => layer.setStyle({{ color: c }}));
                        }}
                    }});

                    polyline.bindTooltip(`Section ${{section.id}}<br>${{section.length_m.toFixed(1)}}m`, {{ sticky: true }});

                    sectionGroup.addLayer(polyline);
                }}

                sectionGroup.addTo(layers.tracks);
                MapApp.sectionIndex.set(section.id, {{ regionId, polyline: sectionGroup, metadata: section, isIndustry, originalColor: trackColor }});
            }}

            // Render signals
            for (const signal of data.signals) {{
                const marker = createSignalMarker(signal);
                marker.addTo(layers.signals);
                MapApp.signalLayers.push(marker);
                MapApp.signalIndex.set(signal.id, {{ regionId, marker, metadata: signal }});
            }}

            // Render industries
            for (let i = 0; i < data.industries.length; i++) {{
                const ind = data.industries[i];
                const marker = L.marker([ind.lon, ind.lat], {{  // [y, x]
                    icon: createIndustryIcon(ind.tag, true, false)
                }}).addTo(layers.industries);

                // Build detailed industry popup
                let industryPopup = `<b>${{ind.name}}</b><br>`;
                industryPopup += `Tag: ${{ind.tag}}<br>`;
                industryPopup += `Local: ${{ind.local_name || 'N/A'}}<br>`;
                if (ind.track_sections && ind.track_sections.length > 0) {{
                    industryPopup += `Track Sections: ${{ind.track_sections.join(', ')}}`;
                }}
                marker.bindPopup(industryPopup, {{maxWidth: 300}});

                MapApp.industryLayers.push(marker);
                MapApp.industryIndex.push({{ region_id: regionId, data: ind }});

                // Track marker for highlighting - use index to ensure unique keys
                const compositeKey = `${{regionId}}_${{i}}`;
                MapApp.industryMarkers.set(compositeKey, {{
                    marker: marker,
                    data: ind,
                    regionId: regionId
                }});

                // Track unique local symbols for the filter dropdown
                if (ind.local_name) {{
                    MapApp.localSymbolIndex.add(ind.local_name);
                }}

                // Track which sections are industry tracks
                if (ind.track_sections) {{
                    for (const sectionId of ind.track_sections) {{
                        MapApp.industrySectionIds.add(`${{regionId}}_${{sectionId}}`);
                    }}
                }}
            }}

            // Update local symbol dropdown after loading industries
            updateLocalSymbolDropdown();

            // Render AI locations
            for (const loc of data.ai_locations) {{
                const marker = L.circleMarker([loc.lon, loc.lat], {{  // [y, x]
                    radius: 6,
                    fillColor: '#ff6600',
                    color: '#cc4400',
                    weight: 2,
                    fillOpacity: 0.8
                }}).addTo(layers.aiLocations);
                marker.bindTooltip(`${{loc.name}}<br>${{loc.type_name}}`, {{ sticky: true }});
                MapApp.aiLayers.push(marker);
                MapApp.aiLocationIndex.push({{ regionId, data: loc }});
            }}

            // Render tile boundaries
            for (const tile of data.tiles) {{
                // In tile-based mode, tile bounds are in world coordinates
                // lat_south/north are y_min/y_max, lon_west/east are x_min/x_max
                const bounds = [[tile.lat_south, tile.lon_west], [tile.lat_north, tile.lon_east]];
                const rect = L.rectangle(bounds, {{
                    color: tile.is_corrected ? 'red' : 'black',
                    fill: false,
                    weight: 1,
                    opacity: 0.5
                }}).addTo(layers.tiles);
                rect.bindTooltip(`Tile ${{tile.x}}, ${{tile.z}}`, {{ sticky: true }});
                MapApp.tileLayers.push(rect);

                // Draw center dot
                const centerY = (tile.lat_north + tile.lat_south) / 2;
                const centerX = (tile.lon_east + tile.lon_west) / 2;
                const dotColor = tile.is_corrected ? 'red' : 'darkgrey';
                const dotFill = tile.is_corrected ? 'red' : 'white';

                const dot = L.circleMarker([centerY, centerX], {{
                    radius: 3,
                    color: dotColor,
                    fillColor: dotFill,
                    fillOpacity: 1.0,
                    weight: 1
                }}).addTo(layers.tiles);
                dot.bindTooltip(`Tile ${{tile.x}}, ${{tile.z}}`, {{ sticky: true }});
                MapApp.tileLayers.push(dot);
            }}

            // Render trains / rail vehicles (from an optional world save)
            renderTrains(regionId, data, layers.trains);

            MapApp.loadedRegions.set(regionId, {{ data, layers, visible: true }});

            // Apply overlay states
            if (MapApp.overlayStates.signals) layers.signals.addTo(MapApp.map);
            if (MapApp.overlayStates.industries) layers.industries.addTo(MapApp.map);
            if (MapApp.overlayStates.aiLocations) layers.aiLocations.addTo(MapApp.map);
            if (MapApp.overlayStates.tileBoundaries) layers.tiles.addTo(MapApp.map);
            if (MapApp.overlayStates.trains) layers.trains.addTo(MapApp.map);

        }} catch (error) {{
            console.error(`Failed to load region ${{regionId}}:`, error);
        }}
    }}

    // Body colours come from the config ([colors] train / train_loco).

    // Draw each rail vehicle as a body polyline spanning its two trucks. Coords
    // are stored [x, y]; Leaflet wants [y, x], so swap (same as tracks). The
    // align transform has already been applied to the body points in
    // transformData, so vehicles ride the manual alignment for free.
    function renderTrains(regionId, data, layerGroup) {{
        // Idempotent per region: drop any prior entries (e.g. a re-align rebuild)
        // so search does not accumulate stale, detached vehicles.
        MapApp.trainIndex = MapApp.trainIndex.filter(it => it.regionId !== regionId);
        for (const train of (data.trains || [])) {{
            for (const v of train.vehicles) {{
                if (!v.resolved || !v.body || v.body.length < 2) continue;
                const coords = v.body.map(p => [p[1], p[0]]);
                const isLoco = /DieselEngine|Electric|Steam|Engine/i.test(v.unit_type || '');
                const _ctc = (MapApp.manifest && MapApp.manifest.car_type_colors) || {{}};
                const _lcc = (MapApp.manifest && MapApp.manifest.loco_company_colors) || {{}};
                const _bodyColor = isLoco
                    ? (_lcc[String(v.company || '').toLowerCase()] || COLORS.trainLoco)
                    : (_ctc[String(v.car_type || '').toLowerCase()] || COLORS.train);
                const line = L.polyline(coords, {{
                    color: _bodyColor,
                    weight: TRAIN_STYLE.car,
                    opacity: 0.95,
                    // Locos get rounded end-caps (a pill shape) so they read as
                    // the powered unit without relying on colour; cars stay blunt.
                    lineCap: isLoco ? 'round' : 'butt'
                }});
                const tip = `<div style="font-family:monospace;white-space:pre;margin:0">`
                    + `Train  : ${{train.train_id}}<br>`
                    + `RV num : ${{v.unit_number || ''}}<br>`
                    + `RV tag : ${{v.destination_tag || ''}}<br>`
                    + `RV typ : ${{v.car_type || ''}}</div>`;
                line.bindTooltip(tip, {{ sticky: true }});
                line.bindPopup(trainVehiclePopup(train, v), {{ maxWidth: 300 }});
                line.addTo(layerGroup);
                MapApp.trainLayers.push(line);
                MapApp.trainIndex.push({{ regionId, trainId: train.train_id, vehicle: v, layer: line }});
            }}
        }}
    }}

    function trainVehiclePopup(train, v) {{
        let html = `<b>Train ${{train.train_id}}</b>${{train.was_ai ? ' (AI)' : ''}}<br>`;
        html += `Unit: ${{v.unit_number || 'N/A'}}<br>`;
        html += `Type: ${{v.unit_type || 'N/A'}}<br>`;
        if (v.car_type) html += `Car type: ${{v.car_type}}<br>`;
        html += `Destination: ${{v.destination_tag || 'N/A'}}<br>`;
        if (v.rv_filename) html += `<span style="color:#888;font-size:11px;">${{v.rv_filename}}</span>`;
        return html;
    }}

    function createSignalMarker(signal) {{
        // Signal as directional triangle
        const size = 12;
        // Add Math.PI (180°) to match geographic mode rotation convention
        const rotation = (signal.rotation * Math.PI / 180) + Math.PI;
        const cos = Math.cos(rotation);
        const sin = Math.sin(rotation);

        // Triangle pointing in direction of rotation
        const points = [
            [0, -size],        // Tip
            [-size/2, size/2], // Left base
            [size/2, size/2]   // Right base
        ].map(([x, y]) => [
            x * cos - y * sin,
            x * sin + y * cos
        ]);

        const lat = signal.lon;  // y coordinate
        const lng = signal.lat;  // x coordinate

        const fillColor = signal.type === 'absolute' ? COLORS.signalAbsolute : COLORS.signalIntermediate;
        // Use stacked border if multi-head signal (stacked_ids has more than one ID)
        const isStacked = signal.stacked_ids && signal.stacked_ids.length > 1;
        const borderColor = isStacked ? COLORS.signalBorderStacked : COLORS.signalBorderSingle;

        const svgIcon = L.divIcon({{
            className: 'signal-icon',
            html: `<svg width="${{size*2}}" height="${{size*2}}" style="overflow:visible;">
                     <polygon points="${{points.map(p => `${{p[0]+size}},${{p[1]+size}}`).join(' ')}}"
                              fill="${{fillColor}}" stroke="${{borderColor}}" stroke-width="2"/>
                   </svg>`,
            iconSize: [size*2, size*2],
            iconAnchor: [size, size]
        }});

        const marker = L.marker([lat, lng], {{ icon: svgIcon }});
        // Show all stacked signal IDs in tooltip for multi-head signals
        let tooltipText = isStacked
            ? `Signals ${{signal.stacked_ids.join(', ')}}<br>${{signal.type}}`
            : `Signal ${{signal.id}}<br>${{signal.type}}`;
        marker.bindTooltip(tooltipText, {{ sticky: true }});
        return marker;
    }}

    function handleTrackClick(e, section, regionId, sectionGroup) {{
        if (e.originalEvent.shiftKey) {{
            // Shift+click: toggle multi-section selection
            if (MapApp.selectedSections.has(section.id)) {{
                MapApp.selectedSections.delete(section.id);
                const color = section.is_switch ? COLORS.switch : COLORS.track;
                sectionGroup.eachLayer(layer => layer.setStyle({{ color: color }}));
            }} else {{
                MapApp.selectedSections.set(section.id, {{ polyline: sectionGroup, metadata: section }});
                sectionGroup.eachLayer(layer => layer.setStyle({{ color: COLORS.trackSelected }}));
            }}
            updateSelectionInfo();
        }} else if (e.originalEvent.ctrlKey) {{
            // Ctrl+click: detailed info popup
            const sectionType = section.is_switch ? 'Switch/Turnout' : 'Track Section';
            let content = `<b>Section ${{section.id}}</b> (${{sectionType}})<br>`;
            content += `Length: ${{section.length_ft.toFixed(1)}} ft (${{section.length_m.toFixed(1)}} m)<br>`;
            content += `Track Type: ${{section.track_type}}<br>`;
            content += `Retarder: ${{section.retarder_mph}}`;
            L.popup({{maxWidth: 300}}).setLatLng(e.latlng).setContent(content).openOn(MapApp.map);
        }} else {{
            // Normal click: basic popup
            const content = `<b>Section ${{section.id}}</b><br>
                            Length: ${{section.length_m.toFixed(1)}}m (${{section.length_ft.toFixed(1)}}ft)<br>
                            ${{section.is_switch ? 'Switch/Turnout' : 'Track Section'}}`;
            L.popup().setLatLng(e.latlng).setContent(content).openOn(MapApp.map);
        }}
    }}

    function updateSelectionInfo() {{
        const count = MapApp.selectedSections.size;
        const info = document.getElementById('selection-info');
        const gradientRow = document.getElementById('gradient-row');
        const gradientSpan = document.getElementById('selection-gradient');

        if (count === 0) {{
            info.style.display = 'none';
            if (gradientRow) gradientRow.style.display = 'none';
            return;
        }}

        info.style.display = 'block';
        document.getElementById('selection-count').textContent = `${{count}} section${{count > 1 ? 's' : ''}}`;

        let totalLengthFt = 0;
        MapApp.selectedSections.forEach(s => totalLengthFt += s.metadata.length_ft);
        document.getElementById('selection-length').textContent = `${{totalLengthFt.toFixed(1)}} ft`;

        // Gradient: elevation from first-clicked section start to last-clicked section end
        const entries = Array.from(MapApp.selectedSections.values());
        if (entries.length >= 2 && gradientRow && gradientSpan) {{
            const firstMeta = entries[0].metadata;
            const lastMeta = entries[entries.length - 1].metadata;
            const totalLengthM = totalLengthFt / 3.28084;
            const elevDiff = lastMeta.elevation_end_m - firstMeta.elevation_start_m;
            const gradientPct = totalLengthM > 0 ? (elevDiff / totalLengthM) * 100 : 0;
            const sign = gradientPct > 0 ? '+' : '';
            gradientSpan.textContent = `${{sign}}${{gradientPct.toFixed(2)}}%`;
            gradientRow.style.display = 'block';
        }} else if (gradientRow) {{
            gradientRow.style.display = 'none';
        }}
    }}

    window.clearSelection = function() {{
        MapApp.selectedSections.forEach((s) => {{
            s.polyline.setStyle({{ color: s.metadata.is_switch ? COLORS.switch : COLORS.track }});
        }});
        MapApp.selectedSections.clear();
        updateSelectionInfo();
    }};

    function toggleRegion(regionId, enabled) {{
        if (enabled) {{
            if (!MapApp.loadedRegions.has(regionId)) {{
                loadRegion(regionId);
            }} else {{
                const region = MapApp.loadedRegions.get(regionId);
                region.layers.tracks.addTo(MapApp.map);
                if (MapApp.overlayStates.signals) region.layers.signals.addTo(MapApp.map);
                if (MapApp.overlayStates.industries) region.layers.industries.addTo(MapApp.map);
                if (MapApp.overlayStates.aiLocations) region.layers.aiLocations.addTo(MapApp.map);
                if (MapApp.overlayStates.tileBoundaries) region.layers.tiles.addTo(MapApp.map);
                if (MapApp.overlayStates.trains) region.layers.trains.addTo(MapApp.map);
                region.visible = true;

                // Rebuild local symbol index when showing region
                rebuildLocalSymbolIndex();
                updateLocalSymbolDropdown();
            }}
        }} else {{
            const region = MapApp.loadedRegions.get(regionId);
            if (region) {{
                MapApp.map.removeLayer(region.layers.tracks);
                MapApp.map.removeLayer(region.layers.signals);
                MapApp.map.removeLayer(region.layers.industries);
                MapApp.map.removeLayer(region.layers.aiLocations);
                MapApp.map.removeLayer(region.layers.tiles);
                MapApp.map.removeLayer(region.layers.trains);
                region.visible = false;

                // Rebuild local symbol index from visible regions only
                rebuildLocalSymbolIndex();
                updateLocalSymbolDropdown();
            }}
        }}
    }}

    function rebuildLocalSymbolIndex() {{
        MapApp.localSymbolIndex.clear();
        for (const [regionId, region] of MapApp.loadedRegions) {{
            if (!region.visible) continue;
            for (const item of MapApp.industryIndex) {{
                if (item.region_id === regionId && item.data.local_name) {{
                    MapApp.localSymbolIndex.add(item.data.local_name);
                }}
            }}
        }}
    }}

    // ========================================
    // Area/place labels (user-defined)
    // ========================================
    function createAreaLabelIcon(area) {{
        const _cp = (MapApp.manifest && MapApp.manifest.color_presets) || {{}};
        const _resolved = area.color ? (_cp[String(area.color).trim().toLowerCase()] || String(area.color).trim()) : '';
        const _lt = (MapApp.manifest && MapApp.manifest.label_types) || [];
        const _tc = (_lt.find(x => x.id === String(area.type || '').toLowerCase()) || {{}}).color || '#ffffff';
        const color = _resolved || _tc;
        const fontSize = area.font_size || 22;
        let style = `display:inline-block;color:${{color}};font-size:${{fontSize}}px;font-weight:bold;white-space:nowrap;`;
        if (area.box) {{
            style += `background:rgba(0,0,0,0.6);padding:2px 6px;border-radius:3px;text-shadow:0 1px 2px rgba(0,0,0,0.8);`;
        }} else {{
            // No box (default): dark outline keeps the text legible over the map.
            style += `text-shadow:-1px -1px 0 #000,1px -1px 0 #000,-1px 1px 0 #000,1px 1px 0 #000;`;
        }}
        // Center the label on its point; rotate around that center if requested.
        const rot = area.rotation ? ` rotate(${{area.rotation}}deg)` : '';
        style += `transform:translate(-50%,-50%)${{rot}};`;
        // iconSize:null lets the container shrink-wrap the text so the box (when
        // enabled) covers the whole label and centering stays correct.
        return L.divIcon({{
            className: 'area-label-marker',
            html: `<div style="${{style}}">${{area.label}}</div>`,
            iconSize: null,
            iconAnchor: [0, 0]
        }});
    }}

    // Forward transform: tile + Run8 local coords -> world meters (matches
    // convert_run8_to_tile_coords in region_extractor.py).
    function areaToWorld(area, tp) {{
        const homeX = tp.home_tile[0];
        const homeZ = tp.home_tile[1];
        const worldX = (area.tile_x - homeX) * tp.tile_width + area.local_x;
        const worldY = (area.tile_z - homeZ) * tp.tile_height - area.local_z;
        return [worldX, worldY];
    }}

    function buildAreaLabels() {{
        MapApp.areaLabelsLayer = L.layerGroup();
        MapApp.areaMarkers = [];
        const areas = (MapApp.manifest && MapApp.manifest.areas) || [];
        const tp = MapApp.manifest && MapApp.manifest.tile_params;
        if (!tp) {{
            if (areas.length > 0) console.warn('Area labels present but manifest has no tile_params; skipping.');
            return;
        }}
        for (const area of areas) {{
            const wc = areaToWorld(area, tp);
            const marker = L.marker([wc[1], wc[0]], {{ icon: createAreaLabelIcon(area) }});
            MapApp.areaLabelsLayer.addLayer(marker);
            MapApp.areaMarkers.push({{ marker: marker, baseFont: area.font_size || 22 }});
        }}
        if (MapApp.overlayStates.areaLabels) MapApp.areaLabelsLayer.addTo(MapApp.map);
        updateAreaLabelSizes();
    }}

    // Scale label text by absolute map scale (meters-per-pixel), matching the
    // on-screen scale bar: full size at/below ~50 m scale, shrinking to a tiny
    // floor at/above ~15 km. Interpolated on log(scale) since scale is
    // exponential in zoom.
    const AREA_LABEL_SCALE_MAX_M = 50;      // scale bar <= this -> full (baseFont) size
    const AREA_LABEL_SCALE_MIN_M = 15000;   // scale bar >= this -> minimum size
    const AREA_LABEL_MIN_PX = 6;            // "too small to read" floor
    function updateAreaLabelSizes() {{
        if (!MapApp.areaMarkers) return;
        // Scale-bar meters ~= 100 px * meters-per-pixel; mpp = 2^-zoom in CRS.Simple.
        const scaleM = 100 * Math.pow(2, -MapApp.map.getZoom());
        const lo = Math.log(AREA_LABEL_SCALE_MAX_M), hi = Math.log(AREA_LABEL_SCALE_MIN_M);
        let t = (Math.log(scaleM) - lo) / (hi - lo);
        t = Math.max(0, Math.min(1, t));    // 0 at 50 m (zoomed in), 1 at 15 km (zoomed out)
        for (const rec of MapApp.areaMarkers) {{
            const el = rec.marker.getElement();
            if (!el || !el.firstChild) continue;
            const px = rec.baseFont + t * (AREA_LABEL_MIN_PX - rec.baseFont);
            el.firstChild.style.fontSize = px.toFixed(1) + 'px';
        }}
    }}

    // Shift+Click capture. First click sets the position (inverting the
    // world-meter transform to recover tile + local coords); the next click sets
    // the text angle along a track, or Esc leaves it horizontal.
    function startAreaCapture(latlng) {{
        const tp = MapApp.manifest && MapApp.manifest.tile_params;
        if (!tp) {{
            alert('Tile parameters are not available in this map, so a label position cannot be captured.');
            return;
        }}
        const homeX = tp.home_tile[0];
        const homeZ = tp.home_tile[1];
        const worldX = latlng.lng;
        const worldY = latlng.lat;
        const tileX = homeX + Math.floor(worldX / tp.tile_width);
        const tileZ = homeZ + Math.floor(worldY / tp.tile_height);
        const localX = worldX - (tileX - homeX) * tp.tile_width;
        const localZ = -(worldY - (tileZ - homeZ) * tp.tile_height);

        MapApp.areaCapture = {{ pending: true, latlng: latlng, tileX: tileX, tileZ: tileZ, localX: localX, localZ: localZ }};

        // Guide line from the anchor to the cursor while choosing the angle.
        MapApp.areaGuide = L.polyline([latlng, latlng], {{ color: '#ffd11a', weight: 2, dashArray: '5,5' }}).addTo(MapApp.map);
        MapApp._areaGuideMove = (ev) => {{ if (MapApp.areaGuide) MapApp.areaGuide.setLatLngs([latlng, ev.latlng]); }};
        MapApp.map.on('mousemove', MapApp._areaGuideMove);

        // Esc = leave the angle horizontal.
        MapApp._areaEsc = (ev) => {{
            if (ev.key === 'Escape' && MapApp.areaCapture && MapApp.areaCapture.pending) {{
                ev.preventDefault();
                finalizeAreaCapture(null);
            }}
        }};
        document.addEventListener('keydown', MapApp._areaEsc);

        showAreaHint('Click a second point along the track to set the text angle &nbsp;&middot;&nbsp; Esc = horizontal');
    }}

    function finalizeAreaCapture(secondLatLng) {{
        const cap = MapApp.areaCapture;
        if (!cap || !cap.pending) return;
        cap.pending = false;

        if (MapApp.areaGuide) {{ MapApp.map.removeLayer(MapApp.areaGuide); MapApp.areaGuide = null; }}
        if (MapApp._areaGuideMove) {{ MapApp.map.off('mousemove', MapApp._areaGuideMove); MapApp._areaGuideMove = null; }}
        if (MapApp._areaEsc) {{ document.removeEventListener('keydown', MapApp._areaEsc); MapApp._areaEsc = null; }}
        hideAreaHint();

        let rotation = 0;
        if (secondLatLng) {{
            const dx = secondLatLng.lng - cap.latlng.lng;
            const dy = secondLatLng.lat - cap.latlng.lat;
            if (dx !== 0 || dy !== 0) {{
                // Screen is north-up; CSS rotate is clockwise with the y-axis pointing down.
                let deg = Math.atan2(-dy, dx) * 180 / Math.PI;
                // A track line has no direction, so fold to [-90, 90] to keep text upright.
                if (deg > 90) deg -= 180;
                if (deg < -90) deg += 180;
                rotation = Math.round(deg);
            }}
        }}
        openAreaLabelPopup(cap, rotation);
    }}

    function showAreaHint(html) {{
        let el = document.getElementById('area-capture-hint');
        if (!el) {{
            el = document.createElement('div');
            el.id = 'area-capture-hint';
            el.style.cssText = 'position:absolute;top:10px;left:50%;transform:translateX(-50%);z-index:2500;'
                + 'background:rgba(0,0,0,0.8);color:#fff;font-family:Arial,sans-serif;font-size:13px;'
                + 'padding:6px 12px;border-radius:4px;pointer-events:none;';
            document.body.appendChild(el);
        }}
        el.innerHTML = html;
        el.style.display = 'block';
    }}

    function hideAreaHint() {{
        const el = document.getElementById('area-capture-hint');
        if (el) el.style.display = 'none';
    }}

    function openAreaLabelPopup(cap, rotation) {{
        const tileX = cap.tileX, tileZ = cap.tileZ, localX = cap.localX, localZ = cap.localZ;
        const html = `
            <div style="min-width:230px;font-family:Arial,sans-serif;font-size:12px;">
                <b>New Area Label</b><br>
                <label style="display:block;margin:6px 0 2px;">Label text:</label>
                <input id="al-text" type="text" placeholder="e.g. Barstow Yard"
                       style="width:100%;box-sizing:border-box;padding:4px;">
                <div style="margin-top:6px;color:#555;">
                    tile ${{tileX}},${{tileZ}} &nbsp; local ${{localX.toFixed(1)}},${{localZ.toFixed(1)}} &nbsp; rot ${{rotation}}&deg;
                </div>
                <button id="al-gen" style="margin-top:8px;padding:4px 8px;cursor:pointer;">Generate INI</button>
                <pre id="al-out" style="display:none;white-space:pre-wrap;background:#f4f4f4;padding:6px;margin-top:6px;border-radius:4px;font-size:11px;"></pre>
                <button id="al-copy" style="display:none;margin-top:4px;padding:4px 8px;cursor:pointer;">Copy to clipboard</button>
            </div>`;

        L.popup({{ maxWidth: 340 }})
            .setLatLng(cap.latlng)
            .setContent(html)
            .openOn(MapApp.map);

        setTimeout(() => {{
            const textEl = document.getElementById('al-text');
            const genBtn = document.getElementById('al-gen');
            const outEl = document.getElementById('al-out');
            const copyBtn = document.getElementById('al-copy');
            if (!textEl || !genBtn) return;
            textEl.focus();

            const generate = () => {{
                const label = (textEl.value || '').trim();
                let slug = label.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
                if (!slug) slug = `area_${{tileX}}_${{tileZ}}`;
                let ini =
                    `[area.${{slug}}]\n` +
                    `label = ${{label || 'New Label'}}\n` +
                    `tile = ${{tileX}},${{tileZ}}\n` +
                    `local = ${{localX.toFixed(1)}},${{localZ.toFixed(1)}}`;
                if (rotation) ini += `\nrotation = ${{rotation}}`;
                outEl.textContent = ini;
                outEl.style.display = 'block';
                copyBtn.style.display = 'inline-block';
            }};

            genBtn.addEventListener('click', generate);
            textEl.addEventListener('keydown', (ev) => {{ if (ev.key === 'Enter') {{ ev.preventDefault(); generate(); }} }});
            copyBtn.addEventListener('click', () => {{
                const text = outEl.textContent;
                if (navigator.clipboard && navigator.clipboard.writeText) {{
                    navigator.clipboard.writeText(text).then(() => {{
                        copyBtn.textContent = 'Copied!';
                        setTimeout(() => {{ copyBtn.textContent = 'Copy to clipboard'; }}, 1200);
                    }});
                }} else {{
                    const range = document.createRange();
                    range.selectNodeContents(outEl);
                    const sel = window.getSelection();
                    sel.removeAllRanges();
                    sel.addRange(range);
                }}
            }});
        }}, 0);
    }}

    function toggleOverlay(overlay, enabled) {{
        MapApp.overlayStates[overlay] = enabled;

        if (overlay === 'areaLabels') {{
            if (MapApp.areaLabelsLayer) {{
                if (enabled) {{ MapApp.areaLabelsLayer.addTo(MapApp.map); updateAreaLabelSizes(); }}
                else MapApp.map.removeLayer(MapApp.areaLabelsLayer);
            }}
            return;
        }}

        MapApp.loadedRegions.forEach((region) => {{
            if (!region.visible) return;

            const layerMap = {{
                signals: region.layers.signals,
                industries: region.layers.industries,
                aiLocations: region.layers.aiLocations,
                tileBoundaries: region.layers.tiles,
                trains: region.layers.trains
            }};

            const layer = layerMap[overlay];
            if (enabled) layer.addTo(MapApp.map);
            else MapApp.map.removeLayer(layer);
        }});

        // Update track colors for industry overlay
        if (overlay === 'industries') {{
            if (enabled && MapApp.currentLocalFilter) {{
                // Apply local filter highlighting if a filter is active
                applyLocalSymbolHighlighting();
            }} else {{
                MapApp.sectionIndex.forEach((s) => {{
                    if (s.isIndustry && !MapApp.selectedSections.has(s.metadata.id)) {{
                        const color = enabled ? COLORS.industryTrack : s.originalColor;
                        s.polyline.eachLayer(layer => layer.setStyle({{ color: color }}));
                    }}
                }});
            }}
        }}
    }}

    function fitBoundsToData() {{
        const bounds = [];
        MapApp.loadedRegions.forEach((region) => {{
            region.data.sections.forEach((section) => {{
                section.paths.forEach((path) => {{
                    path.forEach((p) => bounds.push([p[1], p[0]]));  // [y, x]
                }});
            }});
        }});

        // Include area labels so a newly-added label is within the initial view
        // (regions are loaded selectively, so a label may sit outside the loaded track).
        const tp = MapApp.manifest && MapApp.manifest.tile_params;
        if (tp) {{
            for (const area of (MapApp.manifest.areas || [])) {{
                const wc = areaToWorld(area, tp);
                bounds.push([wc[1], wc[0]]);  // [y, x]
            }}
        }}

        if (bounds.length > 0) {{
            MapApp.map.fitBounds(bounds);
        }}
    }}

    // Local Symbol Filtering Functions
    function createIndustryIcon(tag, isHighlighted, filterActive) {{
        let style;

        if (!filterActive) {{
            // No filter active - normal style
            style = `color:${{COLORS.industryTrack}};font-size:11px;font-weight:bold;white-space:nowrap;text-shadow:-1px -1px 0 #fff,1px -1px 0 #fff,-1px 1px 0 #fff,1px 1px 0 #fff;`;
        }} else if (isHighlighted) {{
            // Filter active AND this matches - highlighted style
            style = `color:#FF4500;font-size:14px;font-weight:bold;white-space:nowrap;text-shadow:-1px -1px 0 #fff,1px -1px 0 #fff,-1px 1px 0 #fff,1px 1px 0 #fff;background:rgba(255,255,0,0.3);padding:2px 4px;border-radius:3px;`;
        }} else {{
            // Filter active but doesn't match - dimmed style
            style = `color:#888888;font-size:10px;font-weight:normal;white-space:nowrap;text-shadow:none;opacity:0.5;`;
        }}

        return L.divIcon({{
            className: 'industry-marker',
            html: `<div style="${{style}}">${{tag}}</div>`,
            iconAnchor: [0, 0]
        }});
    }}

    function updateLocalSymbolDropdown() {{
        const select = document.getElementById('local-symbol-select');
        if (!select) return;

        const currentValue = select.value;

        // Clear existing options except "All"
        while (select.options.length > 1) {{
            select.remove(1);
        }}

        // Get sorted list of local symbols
        const symbols = Array.from(MapApp.localSymbolIndex).sort();

        // Add options
        for (const symbol of symbols) {{
            const option = document.createElement('option');
            option.value = symbol;
            option.textContent = symbol;
            select.appendChild(option);
        }}

        // Restore previous selection if still valid
        if (currentValue && MapApp.localSymbolIndex.has(currentValue)) {{
            select.value = currentValue;
        }} else if (MapApp.currentLocalFilter && !MapApp.localSymbolIndex.has(MapApp.currentLocalFilter)) {{
            // Filter is no longer valid, reset
            MapApp.currentLocalFilter = null;
            select.value = '';
        }}
    }}

    function getIndustriesForSection(regionId, sectionId) {{
        const industries = [];
        for (const item of MapApp.industryIndex) {{
            if (item.region_id === regionId &&
                item.data.track_sections &&
                item.data.track_sections.includes(sectionId)) {{
                industries.push(item.data);
            }}
        }}
        return industries;
    }}

    function applyLocalSymbolHighlighting() {{
        const filterSymbol = MapApp.currentLocalFilter;
        const filterActive = filterSymbol !== null;

        // Update industry markers
        for (const [compositeKey, entry] of MapApp.industryMarkers) {{
            const {{ marker, data, regionId }} = entry;
            const isMatch = filterSymbol ? (data.local_name === filterSymbol) : true;

            // Update marker icon based on match status
            const icon = createIndustryIcon(data.tag, isMatch, filterActive);
            marker.setIcon(icon);
        }}

        // Update track section colors if industries overlay is enabled
        if (MapApp.overlayStates.industries) {{
            for (const [sectionId, data] of MapApp.sectionIndex) {{
                if (!data.isIndustry) continue;
                if (MapApp.selectedSections.has(sectionId)) continue;

                // Find if any industry using this section matches the filter
                const industries = getIndustriesForSection(data.regionId, sectionId);
                let sectionMatches = false;
                if (filterSymbol) {{
                    sectionMatches = industries.some(ind => ind.local_name === filterSymbol);
                }} else {{
                    sectionMatches = true;  // No filter = all match
                }}

                let color;
                if (!filterActive) {{
                    color = COLORS.industryTrack;
                }} else if (sectionMatches) {{
                    color = '#FF4500';  // Orange-red for highlighted
                }} else {{
                    color = '#CCCCCC';  // Gray for non-matching
                }}

                data.polyline.eachLayer(layer => {{
                    if (layer.setStyle) layer.setStyle({{ color: color }});
                }});
            }}
        }}
    }}

    // Search functionality
    window.openSearch = function() {{
        document.getElementById('search-overlay').classList.add('visible');
        document.getElementById('search-dialog').classList.add('visible');
        document.getElementById('search-input').focus();
    }};

    window.closeSearch = function() {{
        document.getElementById('search-overlay').classList.remove('visible');
        document.getElementById('search-dialog').classList.remove('visible');
        document.getElementById('search-input').value = '';
        document.getElementById('search-results').innerHTML = '';
    }};

    // Debounce helper
    let searchTimeout;
    window.performSearch = function() {{
        clearTimeout(searchTimeout);
        searchTimeout = setTimeout(doSearch, 300);
    }};

    function doSearch() {{
        const searchType = document.getElementById('search-type').value;
        const query = document.getElementById('search-input').value.trim().toLowerCase();
        const resultsDiv = document.getElementById('search-results');

        if (!query) {{
            resultsDiv.innerHTML = '';
            return;
        }}

        let results = [];

        if (searchType === 'section') {{
            const queryNum = parseInt(query);
            for (const [sectionId, data] of MapApp.sectionIndex) {{
                if (sectionId.toString().includes(query) || sectionId === queryNum) {{
                    results.push({{
                        type: 'section',
                        id: sectionId,
                        label: `Section ${{sectionId}}`,
                        region: data.regionId,
                        data: data
                    }});
                }}
            }}
        }} else if (searchType === 'signal') {{
            const queryNum = parseInt(query);
            for (const [signalId, data] of MapApp.signalIndex) {{
                if (signalId.toString().includes(query) || signalId === queryNum) {{
                    results.push({{
                        type: 'signal',
                        id: signalId,
                        label: `Signal ${{signalId}}`,
                        region: data.regionId,
                        data: data
                    }});
                }}
            }}
        }} else if (searchType === 'industry') {{
            for (const item of MapApp.industryIndex) {{
                if (item.data.tag.toLowerCase().includes(query) ||
                    item.data.name.toLowerCase().includes(query)) {{
                    results.push({{
                        type: 'industry',
                        id: item.data.tag,
                        label: `${{item.data.tag}} - ${{item.data.name}}`,
                        region: item.region_id,
                        data: item
                    }});
                }}
            }}
        }} else if (searchType === 'aiLocation') {{
            for (const item of MapApp.aiLocationIndex) {{
                if (item.data.name.toLowerCase().includes(query)) {{
                    results.push({{
                        type: 'aiLocation',
                        id: item.data.id,
                        label: `${{item.data.name}} (${{item.data.type_name}})`,
                        region: item.region_id,
                        data: item
                    }});
                }}
            }}
        }} else if (searchType === 'train') {{
            // Match trainID, destinationTag, or unitNumber (substring, case-insensitive)
            for (let i = 0; i < MapApp.trainIndex.length; i++) {{
                const it = MapApp.trainIndex[i];
                const v = it.vehicle;
                const hay = [String(it.trainId), v.unit_number || '', v.destination_tag || '']
                    .join(' ').toLowerCase();
                if (hay.includes(query)) {{
                    results.push({{
                        type: 'train',
                        id: i,
                        label: `Train ${{it.trainId}} &middot; ${{v.unit_number || '?'}}`
                            + (v.destination_tag ? ` &rarr; ${{v.destination_tag}}` : ''),
                        region: it.regionId,
                        data: it
                    }});
                }}
            }}
        }}

        // Limit results
        results = results.slice(0, 50);

        resultsDiv.innerHTML = results.length === 0
            ? '<div style="padding:10px;color:#666;">No results found</div>'
            : results.map(r => {{
                const idStr = typeof r.id === 'string'
                    ? `'${{r.id.replace(/'/g, "\\'")}}'`
                    : r.id;
                return `
                <div class="search-result" onclick="goToResult('${{r.type}}', ${{idStr}}, '${{r.region}}')">
                    <strong>${{r.label}}</strong>
                    <span style="color:#666;font-size:11px;"> (${{r.region}})</span>
                </div>
                `;
            }}).join('');
    }}

    window.goToResult = function(type, id, regionId) {{
        closeSearch();

        if (type === 'section') {{
            const data = MapApp.sectionIndex.get(id);
            if (data) {{
                MapApp.map.fitBounds(data.polyline.getBounds(), {{padding: [50, 50]}});
                // Open tooltip on first layer in the group
                data.polyline.eachLayer(layer => {{
                    if (layer.openTooltip) {{
                        layer.openTooltip();
                        return false; // Stop after first
                    }}
                }});
            }}
        }} else if (type === 'signal') {{
            const data = MapApp.signalIndex.get(id);
            if (data) {{
                // In tile-based mode, coords are [y, x] so we need to use marker position
                MapApp.map.setView(data.marker.getLatLng(), 2);
                data.marker.openTooltip();
            }}
        }} else if (type === 'industry') {{
            const item = MapApp.industryIndex.find(i => i.data.tag === id && i.region_id === regionId);
            if (item) {{
                MapApp.map.setView([item.data.lon, item.data.lat], 16);
            }}
        }} else if (type === 'aiLocation') {{
            const item = MapApp.aiLocationIndex.find(i => i.data.id === id && i.region_id === regionId);
            if (item) {{
                MapApp.map.setView([item.data.lon, item.data.lat], 16);
            }}
        }} else if (type === 'train') {{
            const it = MapApp.trainIndex[id];
            if (it && it.layer) {{
                // Make sure the Trains overlay is visible so the hit is shown.
                if (!MapApp.overlayStates.trains) {{
                    const cb = document.getElementById('toggle-trains');
                    if (cb) cb.checked = true;
                    toggleOverlay('trains', true);
                }}
                MapApp.map.fitBounds(it.layer.getBounds(), {{ padding: [80, 80], maxZoom: 5 }});
                it.layer.openPopup();
            }}
        }}
    }};

    // Initialize
    init();
}})();
</script>
</body>
</html>
'''


def generate_html(config: VisualizationConfig, output_path: Path, tile_based: bool = False) -> None:
    """Generate index.html with Folium map and JavaScript

    Args:
        config: Visualization configuration
        output_path: Path to write HTML file
        tile_based: If True, generate for tile-based coordinates (L.CRS.Simple)
    """
    if tile_based:
        # For tile-based mode, we generate a custom HTML without Folium
        # since Folium doesn't easily support L.CRS.Simple
        html_content = generate_tile_based_html(config)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
        print(f"Generated HTML (tile-based): {output_path}")
        return

    # Geographic mode - use Folium as before
    # Use initial_center from config if specified, otherwise use default
    if config.initial_center:
        center = list(config.initial_center)
    else:
        center = DEFAULT_CENTER
    zoom = DEFAULT_ZOOM

    # Create Folium map with canvas renderer and no default tiles
    # Base layers are handled in JavaScript for proper switching
    m = folium.Map(
        location=center,
        zoom_start=zoom,
        max_zoom=22,
        prefer_canvas=True,
        control_scale=False,  # We add our own scale control
        tiles=None  # No default tiles - we create them in JavaScript
    )

    # Add color configuration and JavaScript
    color_config = generate_color_config(config.colors)
    m.get_root().html.add_child(folium.Element(color_config))
    js_code = generate_javascript()
    m.get_root().html.add_child(folium.Element(js_code))

    # Save the map
    m.save(str(output_path))
    print(f"Generated HTML: {output_path}")


ALIGN_JS = r'''
    // ================= Manual alignment (contiguous tile-based track over real map) =================
    MapApp.align = null; MapApp._raw = {}; MapApp.trackOpacity = 0.8;
    function applyTrackOpacity(){
        const op = MapApp.trackOpacity;
        for (const [id, reg] of MapApp.loadedRegions){
            if (reg.layers && reg.layers.sections){
                reg.layers.sections.eachLayer(sg => {
                    if (sg.eachLayer) sg.eachLayer(pl => { if (pl.setStyle) pl.setStyle({opacity: op}); });
                    else if (sg.setStyle) sg.setStyle({opacity: op});
                });
            }
        }
    }
    function addTrackOpacitySlider(){
        const ctl = document.getElementById('opacity-control');
        if (!ctl) return;
        // Initial track opacity from the config ([visualization] initial_track_opacity).
        if (MapApp.manifest && MapApp.manifest.initial_track_opacity != null)
            MapApp.trackOpacity = MapApp.manifest.initial_track_opacity;
        const div = document.createElement('div');
        div.style.marginTop = '4px';
        div.innerHTML = '<label>Track Opacity: <input type="range" id="track-opacity-slider" min="0" max="100" value="'
            + Math.round(MapApp.trackOpacity * 100) + '"></label>';
        ctl.appendChild(div);
        document.getElementById('track-opacity-slider').addEventListener('input', (e)=>{
            MapApp.trackOpacity = e.target.value/100; applyTrackOpacity();
        });
        applyTrackOpacity();
    }
    function alignInit(){
        const a = MapApp.manifest.align;
        if (!a) return;   // not an alignment build -> behave as a normal geographic viewer
        MapApp.align = { X0:a.X, Y0:a.Y, lat:a.lat, lon:a.lon, cosRef:Math.cos(a.lat_ref*Math.PI/180), scale:1 };
        MapApp.worldToLatLon = function(X, Y){
            const A = MapApp.align, s = A.scale;
            return [ A.lat + s*(Y - A.Y0)/111320, A.lon + s*(X - A.X0)/(111320*A.cosRef) ];
        };
        MapApp.latLngToWorld = function(ll){   // inverse of worldToLatLon (for label capture)
            const A = MapApp.align, s = A.scale;
            return { x: A.X0 + (ll.lng - A.lon)*111320*A.cosRef/s,
                     y: A.Y0 + (ll.lat - A.lat)*111320/s };
        };
        MapApp.transformData = function(d){
            const T = MapApp.worldToLatLon;
            for (const sec of d.sections) for (const p of sec.paths)
                for (let i=0;i<p.length;i++){ const q=T(p[i][0],p[i][1]); p[i]=[q[0],q[1]]; }
            for (const s of (d.signals||[]))     { const q=T(s.lat,s.lon); s.lat=q[0]; s.lon=q[1]; }
            for (const x of (d.ai_locations||[])){ const q=T(x.lat,x.lon); x.lat=q[0]; x.lon=q[1]; }
            for (const x of (d.industries||[]))  { const q=T(x.lat,x.lon); x.lat=q[0]; x.lon=q[1]; }
            for (const t of (d.tiles||[])){
                const sw=T(t.lon_west,t.lat_south), ne=T(t.lon_east,t.lat_north);
                t.lat_south=sw[0]; t.lon_west=sw[1]; t.lat_north=ne[0]; t.lon_east=ne[1];
            }
            for (const tr of (d.trains||[])) for (const v of tr.vehicles)
                for (let i=0;i<(v.body||[]).length;i++){ const q=T(v.body[i][0],v.body[i][1]); v.body[i]=[q[0],q[1]]; }
        };
        MapApp.map.setView([a.lat, a.lon], 12);
        MapApp.authoring = (typeof window.__run8_authoring === 'undefined') ? true : !!window.__run8_authoring;
        MapApp.hasBackend = false;
        buildAlignUI();
        addTrackOpacitySlider();
        MapApp.overlayStates.areaLabels = false;
        addAreaLabelToggle();
        buildAreaLabels();
        // Probe for the optional authoring backend (serve.py) without blocking the
        // initial render; if present, rebuild the labels so their markers become
        // interactive/draggable for editing.
        detectBackend().then(() => { if (MapApp.hasBackend) rebuildAreaLabels(); });
        MapApp.map.on('zoomend', updateAreaLabelSizes);
        MapApp.map.on('contextmenu', onMapContextMenu);   // right-click -> Google Maps
    }

    // ---- Area/place labels (ported from the tile-based viewer; placed via the transform) ----
    function createAreaLabelIcon(area){
        const color = resolveColor(area.color) || labelTypeColor(area.type);
        const fontSize = area.font_size || 22;
        let style = `display:inline-block;color:${color};font-size:${fontSize}px;font-weight:bold;white-space:nowrap;`;
        if (area.box) style += `background:rgba(0,0,0,0.6);padding:2px 6px;border-radius:3px;text-shadow:0 1px 2px rgba(0,0,0,0.8);`;
        else style += `text-shadow:-1px -1px 0 #000,1px -1px 0 #000,-1px 1px 0 #000,1px 1px 0 #000;`;
        const rot = area.rotation ? ` rotate(${area.rotation}deg)` : '';
        style += `transform:translate(-50%,-50%)${rot};`;
        return L.divIcon({ className:'area-label-marker',
            html:`<div style="${style}">${area.label}</div>`, iconSize:null, iconAnchor:[0,0] });
    }
    function areaToWorld(area, tp){   // tile + Run8 local -> world metres (matches convert_run8_to_tile_coords)
        const homeX = tp.home_tile[0], homeZ = tp.home_tile[1];
        return [ (area.tile_x - homeX)*tp.tile_width + area.local_x,
                 (area.tile_z - homeZ)*tp.tile_height - area.local_z ];
    }
    // Label categories: [id, display]. Master "Area Labels" overlay contains one
    // sub-layerGroup per category so each can be shown/hidden independently.
    // Label categories come from the config, emitted as manifest.label_types
    // ([{id,name,color}], in order). A label whose type isn't defined renders white
    // and groups under AREA_UNDEFINED (shown via an auto "Other" toggle when present).
    const AREA_UNDEFINED = '__other__';
    function labelTypes(){ return (MapApp.manifest && MapApp.manifest.label_types) || []; }
    function labelTypeIds(){ return labelTypes().map(t=>t.id); }
    function labelTypeColor(id){ const t=labelTypes().find(x=>x.id===String(id||'').toLowerCase()); return t ? t.color : '#ffffff'; }
    function newLabelType(){ const ids=labelTypeIds(); return ids.indexOf('cp')>=0 ? 'cp' : (ids[0]||'other'); }
    function areaGroupIds(){ return labelTypeIds().concat([AREA_UNDEFINED]); }
    function areaTypeOf(area){ const t=String(area && area.type || '').toLowerCase(); return labelTypeIds().indexOf(t)>=0 ? t : AREA_UNDEFINED; }
    function hasUndefinedLabels(){ const s=new Set(labelTypeIds());
        return ((MapApp.manifest&&MapApp.manifest.areas)||[]).some(a=>!s.has(String(a.type||'').toLowerCase())); }
    // <option>s for a Type <select>: the defined categories plus a trailing "Other"
    // (undefined/white), with the current type preselected.
    function areaTypeSelectOptions(selType){
        const cur = String(selType||'').toLowerCase();
        const defined = labelTypeIds().indexOf(cur) >= 0;
        return labelTypes().map(t=>`<option value="${t.id}"${cur===t.id?' selected':''}>${t.name}</option>`).join('')
             + `<option value="other"${defined?'':' selected'}>Other</option>`;
    }
    // Color palette (name -> hex) from the config, emitted into the manifest.
    // A label's color is stored as a preset NAME or a raw hex; resolve at render.
    function areaColorPresets(){ return (MapApp.manifest && MapApp.manifest.color_presets) || {}; }
    function resolveColor(c){
        if (!c) return c;
        return areaColorPresets()[String(c).trim().toLowerCase()] || String(c).trim();
    }
    function initAreaTypeVisible(){
        if (!MapApp.areaTypeVisible) MapApp.areaTypeVisible = {};
        for (const id of areaGroupIds()) if (!(id in MapApp.areaTypeVisible)) MapApp.areaTypeVisible[id] = true;
    }
    function buildAreaLabels(){
        MapApp.areaLabelsLayer = L.layerGroup();
        MapApp.areaMarkers = [];
        MapApp.areaTypeLayers = {};
        initAreaTypeVisible();
        for (const id of areaGroupIds()){
            const lg = L.layerGroup();
            MapApp.areaTypeLayers[id] = lg;
            if (MapApp.areaTypeVisible[id]) lg.addTo(MapApp.areaLabelsLayer);
        }
        const areas = (MapApp.manifest && MapApp.manifest.areas) || [];
        const tp = MapApp.manifest && MapApp.manifest.tile_params;
        if (tp){ for (const area of areas) addAreaMarker(area); }
        if (MapApp.overlayStates.areaLabels) MapApp.areaLabelsLayer.addTo(MapApp.map);
        updateAreaLabelSizes();
    }
    // Build one label marker; when the authoring backend is present its markers
    // are clickable to open the edit/delete popup (otherwise non-interactive so
    // they never intercept align-drag clicks).
    // Invert the world + alignment transforms to recover the tile/local coords of
    // a lat/lon (shared by new-label capture and drag-to-move).
    function latLngToTileLocal(latlng){
        const tp = MapApp.manifest && MapApp.manifest.tile_params; if (!tp) return null;
        const homeX = tp.home_tile[0], homeZ = tp.home_tile[1];
        const w = MapApp.latLngToWorld(latlng);
        const tileX = homeX + Math.floor(w.x / tp.tile_width);
        const tileZ = homeZ + Math.floor(w.y / tp.tile_height);
        const localX = w.x - (tileX - homeX) * tp.tile_width;
        const localZ = -(w.y - (tileZ - homeZ) * tp.tile_height);
        return { tile_x: tileX, tile_z: tileZ, local_x: localX, local_z: localZ };
    }
    function addAreaMarker(area){
        const tp = MapApp.manifest && MapApp.manifest.tile_params; if (!tp) return null;
        const w = areaToWorld(area, tp);
        const editable = MapApp.authoring && MapApp.hasBackend;
        const marker = L.marker(MapApp.worldToLatLon(w[0], w[1]),
            { icon: createAreaLabelIcon(area), interactive: editable, draggable: editable });
        const rec = { marker, baseFont: area.font_size || 22, world: w, area };
        if (editable){
            marker.on('click', (ev)=>{
                if (MapApp._suppressLabelClick){ MapApp._suppressLabelClick = false; return; }   // trailing click after a rotate
                if (MapApp.areaCapture && MapApp.areaCapture.pending) return;   // mid-placement of a new label
                L.DomEvent.stopPropagation(ev);
                openAreaEditor(Object.assign({}, rec.area), { isNew:false, latlng: marker.getLatLng() });
            });
            // Drag to move: PUT the new tile/local, keeping all other fields. On
            // failure snap back to where the drag started.
            let dragFrom = null;
            marker.on('dragstart', ()=>{ dragFrom = marker.getLatLng(); MapApp.map.closePopup(); });
            marker.on('dragend', ()=>{
                const tl = latLngToTileLocal(marker.getLatLng());
                if (!tl){ if (dragFrom) marker.setLatLng(dragFrom); return; }
                apiArea('PUT', rec.area.id, tl).then(saved => {
                    rec.area = saved;
                    rec.world = areaToWorld(saved, MapApp.manifest.tile_params);
                    marker.setLatLng(MapApp.worldToLatLon(rec.world[0], rec.world[1]));
                    updateAreaLabelSizes();
                }).catch(e => { if (dragFrom) marker.setLatLng(dragFrom); alert('Could not move label: ' + e.message); });
            });
            // Rotate: HOLD the mouse button on the label and scroll the wheel
            // (Shift = 1 deg fine steps). Rotation only happens while the button is
            // held, and the wheel is captured at the window level during the hold so
            // the map never zooms. Bound to the icon element on each (re)add.
            marker.on('add', ()=>{
                const el = marker.getElement();
                if (!el || el.__rotBound) return;
                el.__rotBound = true;
                el.addEventListener('mousedown', (ev)=> startLabelRotate(ev, rec));
            });
        }
        rec.type = areaTypeOf(area);
        (MapApp.areaTypeLayers && MapApp.areaTypeLayers[rec.type] || MapApp.areaLabelsLayer).addLayer(marker);
        MapApp.areaMarkers.push(rec);
        return rec;
    }
    function removeAreaMarker(id){
        if (!MapApp.areaMarkers) return;
        for (let i = MapApp.areaMarkers.length - 1; i >= 0; i--){
            const rec = MapApp.areaMarkers[i];
            if (rec.area.id === id){
                const lg = MapApp.areaTypeLayers && MapApp.areaTypeLayers[rec.type];
                (lg || MapApp.areaLabelsLayer).removeLayer(rec.marker);
                MapApp.areaMarkers.splice(i, 1);
            }
        }
    }
    function replaceAreaMarker(area){ removeAreaMarker(area.id); addAreaMarker(area); updateAreaLabelSizes(); }
    function rebuildAreaLabels(){   // re-create markers (e.g. after backend detection upgrades them to editable)
        if (MapApp.areaLabelsLayer) MapApp.map.removeLayer(MapApp.areaLabelsLayer);
        buildAreaLabels();
    }
    function ensureAreaOverlayVisible(){
        if (MapApp.overlayStates.areaLabels) return;
        MapApp.overlayStates.areaLabels = true;
        const cb = document.getElementById('overlay-areaLabels'); if (cb) cb.checked = true;
        if (MapApp.areaLabelsLayer) MapApp.areaLabelsLayer.addTo(MapApp.map);
    }
    // Make a category visible (used after saving a label so it never lands hidden).
    function ensureAreaTypeVisible(type){
        const t = areaGroupIds().indexOf(String(type||'').toLowerCase()) >= 0 ? String(type||'').toLowerCase() : AREA_UNDEFINED;
        if (!MapApp.areaTypeVisible || MapApp.areaTypeVisible[t]) return;
        setAreaTypeVisible(t, true);
        const cb = document.getElementById('overlay-areaType-'+t); if (cb) cb.checked = true;
    }
    function setAreaTypeVisible(type, on){
        if (!MapApp.areaTypeVisible) return;
        MapApp.areaTypeVisible[type] = on;
        const lg = MapApp.areaTypeLayers && MapApp.areaTypeLayers[type];
        if (lg && MapApp.areaLabelsLayer){
            if (on) MapApp.areaLabelsLayer.addLayer(lg); else MapApp.areaLabelsLayer.removeLayer(lg);
        }
        updateAreaLabelSizes();
    }
    // Hold-button + wheel rotation. mousedown on a label starts a rotate gesture:
    // while the button is held we capture wheel events at the window level (so the
    // map does not zoom) and adjust the label's rotation live; mouseup commits one
    // PUT. If the gesture rotated, the trailing click is suppressed (no editor).
    function startLabelRotate(ev, rec){
        if (!(MapApp.authoring && MapApp.hasBackend)) return;
        if (ev.button !== 0) return;   // left button only
        const st = { rec, wheeled: false };
        const onWheel = (we)=>{
            we.preventDefault(); we.stopPropagation();
            st.wheeled = true;
            const step = we.shiftKey ? 1 : 5;
            let rot = (rec.area.rotation || 0) + (we.deltaY > 0 ? step : -step);
            while (rot > 180) rot -= 360;
            while (rot <= -180) rot += 360;
            rec.area.rotation = rot;
            const el = rec.marker.getElement();
            if (el && el.firstChild) el.firstChild.style.transform = `translate(-50%,-50%) rotate(${rot}deg)`;
            showRotHint(rot);
        };
        const onUp = ()=>{
            window.removeEventListener('wheel', onWheel, { capture: true });
            window.removeEventListener('mouseup', onUp, { capture: true });
            if (st.wheeled){
                MapApp._suppressLabelClick = true;
                setTimeout(()=>{ MapApp._suppressLabelClick = false; }, 50);   // safety net if no click follows
                apiArea('PUT', rec.area.id, { rotation: rec.area.rotation })
                    .then(saved => { rec.area = saved; })
                    .catch(e => alert('Could not rotate label: ' + e.message));
            }
        };
        window.addEventListener('wheel', onWheel, { passive: false, capture: true });
        window.addEventListener('mouseup', onUp, { capture: true });
    }
    function showRotHint(rot){
        showAreaHint(`Rotation: ${rot}&deg; &nbsp;&middot;&nbsp; hold + scroll to adjust, Shift = 1&deg;`);
        if (MapApp._rotHintTimer) clearTimeout(MapApp._rotHintTimer);
        MapApp._rotHintTimer = setTimeout(hideAreaHint, 900);
    }
    // Right-click anywhere -> open that spot in Google Maps in a new tab, carrying
    // the current zoom. Uses the map lat/lon under the cursor (in the align viewer
    // that is the manually-aligned position, so it is only as accurate as the
    // current alignment). Suppresses the browser's native context menu.
    function openInGoogleMaps(latlng){
        const zoom = Math.max(1, Math.min(21, Math.round(MapApp.map.getZoom())));
        const lat = latlng.lat.toFixed(6), lon = latlng.lng.toFixed(6);
        // data=!3m1!1e3 forces the satellite base layer (Google's own satellite toggle).
        const url = `https://www.google.com/maps/place/${lat},${lon}/@${lat},${lon},${zoom}z/data=!3m1!1e3`;
        window.open(url, '_blank', 'noopener');
    }
    function onMapContextMenu(e){
        if (e.originalEvent) L.DomEvent.preventDefault(e.originalEvent);
        openInGoogleMaps(e.latlng);
    }
    // Probe for serve.py; sets MapApp.hasBackend. Fails closed to static mode.
    function detectBackend(){
        return fetch('api/ping').then(r => r.ok ? r.json() : null)
            .then(j => {
                MapApp.hasBackend = !!(j && j.ok);
                // Honor a read-only server (serve.py --no-authoring): hide the Add
                // Label button and make existing labels non-interactive, even though
                // the page was generated as an authoring build.
                if (j && j.ok && j.authoring === false && MapApp.disableAuthoringUI) MapApp.disableAuthoringUI();
                // If the server is watching a world save, poll it so rail-vehicle
                // positions refresh live when the save file is re-written.
                if (j && j.ok && j.world) startTrainsPolling();
            })
            .catch(() => { MapApp.hasBackend = false; });
    }

    // ---- Live rail-vehicle refresh (serve.py --world) ----
    MapApp.trainsVersion = null;
    function pollTrainsOnce(){
        return fetch('api/trains', {cache:'no-store'})
            .then(r => r.ok ? r.json() : null)
            .then(applyLiveTrains)
            .catch(err => console.error('trains poll failed:', err));
    }
    MapApp.pollTrainsOnce = pollTrainsOnce;
    function startTrainsPolling(){
        if (MapApp._trainsPoll) return;
        pollTrainsOnce();
        MapApp._trainsPoll = setInterval(pollTrainsOnce, 3000);
    }
    function applyLiveTrains(j){
        if (!j || !j.trains) return;
        // Whole-save totals are region-independent; update the status line even if
        // the render below early-returns (no regions loaded yet / unchanged version).
        if (j.totals) { MapApp._liveTotals = j.totals; updateTrainCount(); }
        // Regions load asynchronously (and can be toggled on later); re-apply when
        // either the save changed OR the set of loaded regions changed, so the
        // initial poll that arrives before regions finish loading is not lost.
        if (!MapApp.loadedRegions.size) return;
        const regionsKey = [...MapApp.loadedRegions.keys()].sort().join(',');
        if (j.version === MapApp.trainsVersion && regionsKey === MapApp._trainsRegionsKey) return;
        MapApp.trainsVersion = j.version;
        MapApp._trainsRegionsKey = regionsKey;
        const T = MapApp.worldToLatLon;
        for (const [regionId, region] of MapApp.loadedRegions){
            const fresh = j.trains[regionId] || [];
            // Server returns raw world coords; apply the alignment transform (as
            // transformData does for baked data) before rendering.
            for (const tr of fresh) for (const v of tr.vehicles)
                for (let i=0;i<(v.body||[]).length;i++){ const q=T(v.body[i][0], v.body[i][1]); v.body[i]=[q[0],q[1]]; }
            region.data.trains = fresh;
            region.layers.trains.clearLayers();
            region.layers.trainLabels.clearLayers();
            renderTrains(regionId, region.data, region.layers);
            if (MapApp.overlayStates.trains) region.layers.trains.addTo(MapApp.map);
        }
        updateTrainLabelVisibility();
    }
    function apiArea(method, id, body){
        const url = 'api/areas' + (id != null ? '/' + encodeURIComponent(id) : '');
        return fetch(url, { method, headers: {'Content-Type':'application/json'},
                            body: body ? JSON.stringify(body) : undefined })
            .then(async r => { const t = await r.text(); let j = {};
                try { j = t ? JSON.parse(t) : {}; } catch(e){}
                if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status)); return j; });
    }
    function escapeHtml(s){ return String(s == null ? '' : s)
        .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
    function repositionAreaLabels(){   // called after a drag changes the transform
        if (!MapApp.areaMarkers) return;
        for (const rec of MapApp.areaMarkers) rec.marker.setLatLng(MapApp.worldToLatLon(rec.world[0], rec.world[1]));
    }
    // Font scales with map scale (metres-per-pixel), like the scale bar: full size
    // at/below ~50 m, shrinking to a small floor by ~15 km. Web-Mercator mpp here.
    const AREA_LABEL_SCALE_MAX_M = 50, AREA_LABEL_SCALE_MIN_M = 15000, AREA_LABEL_MIN_PX = 6;
    function updateAreaLabelSizes(){
        if (!MapApp.areaMarkers) return;
        const mpp = 156543.03392 * Math.cos(MapApp.map.getCenter().lat*Math.PI/180) / Math.pow(2, MapApp.map.getZoom());
        const scaleM = 100 * mpp;
        const lo = Math.log(AREA_LABEL_SCALE_MAX_M), hi = Math.log(AREA_LABEL_SCALE_MIN_M);
        let t = (Math.log(scaleM) - lo) / (hi - lo); t = Math.max(0, Math.min(1, t));
        for (const rec of MapApp.areaMarkers){
            const el = rec.marker.getElement(); if (!el || !el.firstChild) continue;
            el.firstChild.style.fontSize = (rec.baseFont + t*(AREA_LABEL_MIN_PX - rec.baseFont)).toFixed(1) + 'px';
        }
    }
    function addAreaLabelToggle(){
        const list = document.getElementById('overlay-list');
        if (!list) return;
        initAreaTypeVisible();
        // Master "Area Labels" toggle + a "Filter" button that opens the per-category
        // checkboxes in a popover (keeps the overlay panel tidy).
        const item = document.createElement('div'); item.className = 'overlay-item';
        item.style.cssText = 'display:flex;align-items:center;gap:6px;';
        item.innerHTML = '<input type="checkbox" id="overlay-areaLabels"><label for="overlay-areaLabels">Area Labels</label>'
            + '<button id="area-filter-btn" type="button" title="Choose which label types to show"'
            + ' style="margin-left:auto;font-size:11px;padding:1px 7px;cursor:pointer;">Filter</button>';
        list.appendChild(item);
        item.querySelector('#overlay-areaLabels').addEventListener('change', (e)=>{
            MapApp.overlayStates.areaLabels = e.target.checked;
            if (!MapApp.areaLabelsLayer) return;
            if (e.target.checked){ MapApp.areaLabelsLayer.addTo(MapApp.map); updateAreaLabelSizes(); }
            else MapApp.map.removeLayer(MapApp.areaLabelsLayer);
        });
        item.querySelector('#area-filter-btn').addEventListener('click', (e)=>{ e.stopPropagation(); toggleAreaFilterPopover(e.currentTarget); });
    }
    // Popover listing the label-type checkboxes (built from the config's label types,
    // plus an auto "Other" row when undefined labels exist). Toggles open/closed.
    function closeAreaFilterPopover(){
        const pop = document.getElementById('area-filter-popover');
        if (pop) pop.remove();
        document.removeEventListener('mousedown', areaFilterAway, true);
    }
    function areaFilterAway(e){
        const pop = document.getElementById('area-filter-popover');
        if (pop && !pop.contains(e.target) && e.target.id !== 'area-filter-btn') closeAreaFilterPopover();
    }
    function toggleAreaFilterPopover(anchorBtn){
        if (document.getElementById('area-filter-popover')){ closeAreaFilterPopover(); return; }
        const rows = labelTypes().map(t => [t.id, t.name, t.color]);
        if (hasUndefinedLabels()) rows.push([AREA_UNDEFINED, 'Other', '#ffffff']);
        const pop = document.createElement('div'); pop.id = 'area-filter-popover';
        pop.style.cssText = 'position:fixed;z-index:3000;background:#fff;border:1px solid #888;border-radius:6px;'
            + 'box-shadow:0 2px 12px rgba(0,0,0,.3);padding:8px 10px;font:12px Arial;min-width:150px;';
        pop.innerHTML = '<div style="font-weight:bold;margin-bottom:6px;">Show label types</div>';
        for (const [id,label,color] of rows){
            const row = document.createElement('label');
            row.style.cssText = 'display:flex;align-items:center;gap:6px;padding:2px 0;cursor:pointer;';
            row.innerHTML = '<input type="checkbox" id="overlay-areaType-'+id+'"'+(MapApp.areaTypeVisible[id]?' checked':'')+'>'
                + '<span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:'+(color||'#ffffff')+';border:1px solid rgba(0,0,0,.4);"></span>'
                + label;
            pop.appendChild(row);
            row.querySelector('input').addEventListener('change', (e)=> setAreaTypeVisible(id, e.target.checked));
        }
        const btns = document.createElement('div'); btns.style.cssText = 'margin-top:7px;display:flex;gap:6px;';
        btns.innerHTML = '<button type="button" id="af-all" style="font-size:11px;padding:1px 7px;cursor:pointer;">All</button>'
            + '<button type="button" id="af-none" style="font-size:11px;padding:1px 7px;cursor:pointer;">None</button>';
        pop.appendChild(btns);
        const setAll = (on)=> rows.forEach(([id])=>{
            const cb = pop.querySelector('#overlay-areaType-'+id); if (cb) cb.checked = on;
            setAreaTypeVisible(id, on);
        });
        btns.querySelector('#af-all').addEventListener('click', ()=> setAll(true));
        btns.querySelector('#af-none').addEventListener('click', ()=> setAll(false));
        document.body.appendChild(pop);
        const r = anchorBtn.getBoundingClientRect();
        const pw = pop.offsetWidth;
        let left = r.left; if (left + pw > window.innerWidth - 6) left = window.innerWidth - 6 - pw;
        pop.style.top = (r.bottom + 4) + 'px';
        pop.style.left = Math.max(6, left) + 'px';
        setTimeout(()=> document.addEventListener('mousedown', areaFilterAway, true), 0);
    }

    // ---- Area-label authoring (click to place; second click sets the angle) ----
    function startAreaCapture(latlng){
        const tl = latLngToTileLocal(latlng);
        if (!tl){ alert('Tile parameters are not available, so a label position cannot be captured.'); return; }
        MapApp.areaCapture = { pending:true, latlng, tileX: tl.tile_x, tileZ: tl.tile_z, localX: tl.local_x, localZ: tl.local_z };
        MapApp.areaGuide = L.polyline([latlng, latlng], { color:'#ffd11a', weight:2, dashArray:'5,5' }).addTo(MapApp.map);
        MapApp._areaGuideMove = (ev)=>{ if (MapApp.areaGuide) MapApp.areaGuide.setLatLngs([latlng, ev.latlng]); };
        MapApp.map.on('mousemove', MapApp._areaGuideMove);
        MapApp._areaEsc = (ev)=>{ if (ev.key==='Escape' && MapApp.areaCapture && MapApp.areaCapture.pending){ ev.preventDefault(); finalizeAreaCapture(null); } };
        document.addEventListener('keydown', MapApp._areaEsc);
        showAreaHint('Click a second point along the track to set the text angle &nbsp;&middot;&nbsp; Esc = horizontal');
    }
    function finalizeAreaCapture(secondLatLng){
        const cap = MapApp.areaCapture;
        if (!cap || !cap.pending) return;
        cap.pending = false;
        if (MapApp.areaGuide){ MapApp.map.removeLayer(MapApp.areaGuide); MapApp.areaGuide = null; }
        if (MapApp._areaGuideMove){ MapApp.map.off('mousemove', MapApp._areaGuideMove); MapApp._areaGuideMove = null; }
        if (MapApp._areaEsc){ document.removeEventListener('keydown', MapApp._areaEsc); MapApp._areaEsc = null; }
        hideAreaHint();
        let rotation = 0;
        if (secondLatLng){
            const p1 = MapApp.map.latLngToContainerPoint(cap.latlng);
            const p2 = MapApp.map.latLngToContainerPoint(secondLatLng);
            const dx = p2.x - p1.x, dy = p2.y - p1.y;   // container y is down => CSS-clockwise
            if (dx!==0 || dy!==0){
                let deg = Math.atan2(dy, dx) * 180 / Math.PI;
                if (deg > 90) deg -= 180;
                if (deg < -90) deg += 180;
                rotation = Math.round(deg);
            }
        }
        const area = { label:'', tile_x: cap.tileX, tile_z: cap.tileZ,
                       local_x: +cap.localX.toFixed(1), local_z: +cap.localZ.toFixed(1),
                       rotation, type: newLabelType() };
        openAreaEditor(area, { isNew:true, latlng: cap.latlng });
    }
    function showAreaHint(html){
        let el = document.getElementById('area-capture-hint');
        if (!el){ el = document.createElement('div'); el.id='area-capture-hint';
            el.style.cssText='position:absolute;top:46px;left:50%;transform:translateX(-50%);z-index:2500;'
              +'background:rgba(0,0,0,0.8);color:#fff;font:13px Arial;padding:6px 12px;border-radius:4px;pointer-events:none;';
            document.body.appendChild(el); }
        el.innerHTML = html; el.style.display='block';
    }
    function hideAreaHint(){ const el=document.getElementById('area-capture-hint'); if (el) el.style.display='none'; }
    // Unified label editor. New labels POST; existing labels PUT / DELETE. With
    // no backend, new labels fall back to the copy-paste INI popup below.
    function openAreaEditor(area, opts){
        const isNew = !!opts.isNew;
        if (isNew && !MapApp.hasBackend){ openAreaIniPopup(area, opts.latlng); return; }
        // Color control: Default (category color) / named presets / Custom (RGB).
        // A preset stores its NAME; Custom stores hex; Default stores nothing.
        const presets = areaColorPresets();
        const presetNames = Object.keys(presets);
        const curColor = (area.color || '').trim();
        const curLower = curColor.toLowerCase();
        const colorMode = !curColor ? '__default__' : (presetNames.indexOf(curLower) >= 0 ? curLower : '__custom__');
        const customHex = (colorMode === '__custom__' && /^#[0-9a-fA-F]{6}$/.test(curColor)) ? curColor : '#ffd11a';
        const colorOpts = ['<option value="__default__"' + (colorMode==='__default__'?' selected':'') + '>Default</option>']
            .concat(presetNames.map(n => `<option value="${n}"${colorMode===n?' selected':''}>${n.toUpperCase()}</option>`))
            .concat([`<option value="__custom__"${colorMode==='__custom__'?' selected':''}>Custom…</option>`])
            .join('');
        const html = `<div style="min-width:250px;font:12px Arial;">
            <b>${isNew ? 'New' : 'Edit'} Area Label</b>
            <label style="display:block;margin:6px 0 2px;">Label text:</label>
            <input id="al-text" type="text" placeholder="e.g. Barstow Yard" value="${escapeHtml(area.label)}" style="width:100%;box-sizing:border-box;padding:4px;">
            <div style="display:flex;gap:8px;margin-top:6px;align-items:center;flex-wrap:wrap;">
                <label>Rotation&deg; <input id="al-rot" type="number" value="${area.rotation || 0}" style="width:60px;"></label>
                <label>Color <select id="al-color-sel" style="padding:2px;">${colorOpts}</select></label>
                <span id="al-color-swatch" style="display:inline-block;width:14px;height:14px;border-radius:3px;border:1px solid rgba(0,0,0,.4);"></span>
                <input id="al-color-custom" type="color" value="${customHex}" style="width:34px;height:22px;padding:0;display:${colorMode==='__custom__'?'inline-block':'none'};">
            </div>
            <div style="display:flex;gap:8px;margin-top:6px;align-items:center;flex-wrap:wrap;">
                <label>Font <input id="al-font" type="number" value="${area.font_size || ''}" placeholder="22" style="width:56px;"></label>
                <label><input id="al-box" type="checkbox" ${area.box ? 'checked' : ''}> box</label>
                <label>Type <select id="al-type" style="padding:2px;">${areaTypeSelectOptions(area.type)}</select></label>
            </div>
            <div style="margin-top:6px;color:#555;">tile ${area.tile_x},${area.tile_z} &nbsp; local ${(+area.local_x).toFixed(1)},${(+area.local_z).toFixed(1)}</div>
            ${isNew ? '' : '<div style="margin-top:4px;color:#777;font-style:italic;">Tip: drag to move &middot; hold the mouse button on it and scroll to rotate.</div>'}
            <div style="margin-top:8px;">
                <button id="al-save" style="padding:4px 10px;cursor:pointer;">Save</button>
                ${isNew ? '' : '<button id="al-del" style="margin-left:8px;padding:4px 10px;cursor:pointer;color:#b00;">Delete</button>'}
            </div>
            <div id="al-err" style="color:#b00;margin-top:6px;display:none;"></div></div>`;
        L.popup({ maxWidth: 360 }).setLatLng(opts.latlng).setContent(html).openOn(MapApp.map);
        setTimeout(()=>{
            const textEl = document.getElementById('al-text');
            const saveBtn = document.getElementById('al-save');
            const delBtn = document.getElementById('al-del');
            const errEl = document.getElementById('al-err');
            if (!textEl || !saveBtn) return;
            textEl.focus();
            const showErr = (m)=>{ if (errEl){ errEl.textContent = m; errEl.style.display = 'block'; } };
            const colorSel = document.getElementById('al-color-sel');
            const colorCustom = document.getElementById('al-color-custom');
            const colorSwatch = document.getElementById('al-color-swatch');
            const typeSel = document.getElementById('al-type');
            const swatchColor = ()=>{
                const v = colorSel ? colorSel.value : '__default__';
                if (v === '__default__') return labelTypeColor(typeSel.value);
                if (v === '__custom__') return colorCustom.value;
                return presets[v] || COLORS.areaLabel;
            };
            const syncColorUI = ()=>{
                if (colorCustom) colorCustom.style.display = (colorSel && colorSel.value === '__custom__') ? 'inline-block' : 'none';
                if (colorSwatch) colorSwatch.style.background = swatchColor();
            };
            if (colorSel) colorSel.addEventListener('change', syncColorUI);
            if (colorCustom) colorCustom.addEventListener('input', syncColorUI);
            if (typeSel) typeSel.addEventListener('change', syncColorUI);  // Default swatch tracks the category
            syncColorUI();
            const gather = ()=>{
                const font = (document.getElementById('al-font').value || '').trim();
                const cv = colorSel ? colorSel.value : '__default__';
                const color = (cv === '__default__') ? null : (cv === '__custom__' ? colorCustom.value : cv);
                return {
                    label: (textEl.value || '').trim(),
                    rotation: Number(document.getElementById('al-rot').value) || 0,
                    color: color,
                    font_size: font ? Number(font) : null,
                    box: document.getElementById('al-box').checked,
                    type: typeSel ? typeSel.value : 'other'
                };
            };
            const save = ()=>{
                const body = gather();
                if (!body.label){ showErr('Label text is required.'); return; }
                let req;
                if (isNew){
                    Object.assign(body, { tile_x: area.tile_x, tile_z: area.tile_z,
                                          local_x: area.local_x, local_z: area.local_z });
                    req = apiArea('POST', null, body);
                } else {
                    req = apiArea('PUT', area.id, body);
                }
                saveBtn.disabled = true;
                req.then(saved => {
                    if (isNew) addAreaMarker(saved); else replaceAreaMarker(saved);
                    updateAreaLabelSizes(); ensureAreaOverlayVisible(); ensureAreaTypeVisible(saved.type);
                    MapApp.map.closePopup();
                }).catch(e => { saveBtn.disabled = false; showErr(e.message); });
            };
            saveBtn.addEventListener('click', save);
            textEl.addEventListener('keydown', (ev)=>{ if (ev.key === 'Enter'){ ev.preventDefault(); save(); } });
            if (delBtn){
                delBtn.addEventListener('click', ()=>{
                    if (!confirm('Delete this label?')) return;
                    delBtn.disabled = true;
                    apiArea('DELETE', area.id).then(()=>{ removeAreaMarker(area.id); MapApp.map.closePopup(); })
                        .catch(e => { delBtn.disabled = false; showErr(e.message); });
                });
            }
        }, 0);
    }
    // Fallback for authoring without the backend: paste-ready INI block (legacy flow).
    function openAreaIniPopup(area, latlng){
        const tileX=area.tile_x, tileZ=area.tile_z, localX=area.local_x, localZ=area.local_z, rotation=area.rotation||0;
        const html = `<div style="min-width:230px;font:12px Arial;">
            <b>New Area Label</b><br>
            <label style="display:block;margin:6px 0 2px;">Label text:</label>
            <input id="al-text" type="text" placeholder="e.g. Barstow Yard" style="width:100%;box-sizing:border-box;padding:4px;">
            <label style="display:block;margin:6px 0 2px;">Type <select id="al-type" style="padding:2px;">${areaTypeSelectOptions(area.type)}</select></label>
            <div style="margin-top:6px;color:#555;">tile ${tileX},${tileZ} &nbsp; local ${localX.toFixed(1)},${localZ.toFixed(1)} &nbsp; rot ${rotation}&deg;</div>
            <button id="al-gen" style="margin-top:8px;padding:4px 8px;cursor:pointer;">Generate INI</button>
            <pre id="al-out" style="display:none;white-space:pre-wrap;background:#f4f4f4;padding:6px;margin-top:6px;border-radius:4px;font-size:11px;"></pre>
            <button id="al-copy" style="display:none;margin-top:4px;padding:4px 8px;cursor:pointer;">Copy to clipboard</button></div>`;
        L.popup({ maxWidth:340 }).setLatLng(latlng).setContent(html).openOn(MapApp.map);
        setTimeout(()=>{
            const textEl=document.getElementById('al-text'), genBtn=document.getElementById('al-gen'),
                  outEl=document.getElementById('al-out'), copyBtn=document.getElementById('al-copy');
            if (!textEl || !genBtn) return;
            textEl.focus();
            const generate = ()=>{
                const label=(textEl.value||'').trim();
                let slug=label.toLowerCase().replace(/[^a-z0-9]+/g,'_').replace(/^_+|_+$/g,'');
                if (!slug) slug=`area_${tileX}_${tileZ}`;
                let ini=`[area.${slug}]\nlabel = ${label||'New Label'}\ntile = ${tileX},${tileZ}\nlocal = ${localX.toFixed(1)},${localZ.toFixed(1)}`;
                if (rotation) ini+=`\nrotation = ${rotation}`;
                const atype=(document.getElementById('al-type')||{}).value;
                if (atype && atype!=='other') ini+=`\ntype = ${atype}`;
                outEl.textContent=ini; outEl.style.display='block'; copyBtn.style.display='inline-block';
            };
            genBtn.addEventListener('click', generate);
            textEl.addEventListener('keydown', (ev)=>{ if (ev.key==='Enter'){ ev.preventDefault(); generate(); } });
            copyBtn.addEventListener('click', ()=>{
                const text=outEl.textContent;
                if (navigator.clipboard && navigator.clipboard.writeText){
                    navigator.clipboard.writeText(text).then(()=>{ copyBtn.textContent='Copied!'; setTimeout(()=>{copyBtn.textContent='Copy to clipboard';},1200); });
                }
            });
        }, 0);
    }
    function rerenderAlign(){
        const ids = [...MapApp.loadedRegions.keys()];
        for (const id of ids) {
            unloadRegion(id);
            MapApp.loadedRegions.delete(id);   // force loadRegion to rebuild (not just show old layers)
        }
        // The rebuild renders the BAKED trains from the raw cache; when a world
        // save is being live-watched the poll owns the current positions (and some
        // regions have no baked trains at all), so re-fetch them onto the rebuilt
        // regions once the loads settle - otherwise live trains vanish after align.
        Promise.all(ids.map(id => loadRegion(id))).then(() => {
            if (MapApp._trainsPoll) {
                MapApp.trainsVersion = null;
                MapApp._trainsRegionsKey = null;
                pollTrainsOnce();
            }
        });
    }
    function fitToRegion(regionId){
        // Enabling a region pans/zooms to it (its track can span 100+ mi; the map
        // does not otherwise move, so a newly-enabled region may be off-screen).
        const reg = MapApp.loadedRegions.get(regionId);
        if (!reg || !reg.data) return;
        let mnLa=90,mxLa=-90,mnLo=180,mxLo=-180, any=false;
        for (const sec of reg.data.sections) for (const p of sec.paths) for (const q of p){
            any=true;
            if(q[0]<mnLa)mnLa=q[0]; if(q[0]>mxLa)mxLa=q[0];
            if(q[1]<mnLo)mnLo=q[1]; if(q[1]>mxLo)mxLo=q[1];
        }
        if (any) { MapApp.map.invalidateSize(); MapApp.map.fitBounds([[mnLa,mnLo],[mxLa,mxLo]], {padding:[40,40]}); }
    }
    function buildAlignUI(){
        const bs='position:absolute;top:10px;z-index:1500;padding:6px 10px;cursor:pointer;'+
          'background:#fff;border:1px solid #888;border-radius:6px;box-shadow:0 1px 6px rgba(0,0,0,.3);font:13px Arial';
        const authoring = (typeof window.__run8_authoring === 'undefined') ? true : !!window.__run8_authoring;
        const btn=document.createElement('button'); btn.textContent='Align mode: OFF'; btn.style.cssText=bs+';left:52px';
        document.body.appendChild(btn);
        let lbl=null;
        if (authoring){
            lbl=document.createElement('button'); lbl.textContent='Add Label: OFF'; lbl.style.cssText=bs+';left:170px';
            document.body.appendChild(lbl);
        }
        // Help button + overlay: a quick reference of the map's mouse/key commands.
        const help=document.createElement('button'); help.textContent='Help';
        help.style.cssText=bs+';left:'+(authoring?288:170)+'px';
        document.body.appendChild(help);
        let helpEl=null;
        function kbd(s){ return '<kbd style="background:#eee;border:1px solid #ccc;border-radius:3px;padding:0 5px;font:12px monospace">'+s+'</kbd>'; }
        function hrow(t,d){ return '<dt style="font-weight:600;margin-top:10px">'+t+'</dt>'
            +'<dd style="margin:2px 0 0 0;color:#444">'+d+'</dd>'; }
        function toggleHelp(show){
            if(!helpEl){
                helpEl=document.createElement('div');
                helpEl.style.cssText='position:absolute;inset:0;z-index:3000;display:none;background:rgba(0,0,0,.35)';
                const card=document.createElement('div');
                card.style.cssText='position:absolute;top:50px;left:52px;max-width:430px;max-height:80vh;'
                    +'overflow:auto;background:#fff;border-radius:8px;box-shadow:0 4px 20px rgba(0,0,0,.35);'
                    +'padding:16px 20px;font:13px/1.5 Arial';
                card.innerHTML=
                    '<div style="display:flex;justify-content:space-between;align-items:center;'
                    +'border-bottom:1px solid #ddd;padding-bottom:8px;margin-bottom:6px">'
                    +'<h3 style="margin:0;font:600 15px Arial">Map controls &amp; tips</h3>'
                    +'<button id="help-close" title="Close" style="border:none;background:#eee;border-radius:4px;'
                    +'width:26px;height:26px;cursor:pointer;font-size:16px">&times;</button></div>'
                    +'<dl style="margin:0">'
                    +hrow('Pan / zoom','Drag to pan &middot; scroll wheel to zoom.')
                    +hrow(kbd('Shift')+' + click a track section','Add or remove it from the selection; the panel totals length and average grade.')
                    +hrow(kbd('Ctrl')+' + click a track section','Show a detailed info popup (length, grade, type).')
                    +hrow('Right-click the map','Open that exact point in Google Maps (new tab) to cross-check imagery.')
                    +hrow('Enable a region (checkbox)','Loads the region on demand and fits the map to it.')
                    +hrow('Search button','Find a track section, signal, industry, AI location, or train / rail vehicle.')
                    +hrow('Align mode button','Turn on, then drag the track to slide it onto the real map; release to commit the alignment.')
                    +(authoring ?
                        hrow('Add Label button','Turn on, click to place a label, then click a second point to set the text angle ('+kbd('Esc')+' = horizontal).')
                       +hrow('Click a label','Edit its text, colour, font, rotation or box — or delete it.')
                       +hrow('Drag a label','Move it. Hold the mouse button on a label and scroll the wheel to rotate it ('+kbd('Shift')+' = 1&deg; steps).')
                      : '')
                    +hrow('Opacity sliders (bottom-left)','Independent Map opacity (the base map) and Track opacity.')
                    +'</dl>';
                helpEl.appendChild(card);
                helpEl.addEventListener('click', e=>{ if(e.target===helpEl) toggleHelp(false); });
                card.querySelector('#help-close').onclick=()=> toggleHelp(false);
                document.addEventListener('keydown', e=>{ if(e.key==='Escape' && helpEl.style.display!=='none') toggleHelp(false); });
                document.body.appendChild(helpEl);
            }
            helpEl.style.display = show ? 'block' : 'none';
        }
        help.onclick=()=> toggleHelp(helpEl===null || helpEl.style.display==='none');
        const panes = MapApp.map.getPanes();
        function setT(t){ panes.overlayPane.style.transform=t;
            if(panes.markerPane) panes.markerPane.style.transform=t;
            if(panes.shadowPane) panes.shadowPane.style.transform=t; }
        let aligning=false, drag=null; MapApp.labelMode=false;
        function updA(){ btn.textContent='Align mode: '+(aligning?'ON':'OFF'); btn.style.background=aligning?'#1560d0':'#fff'; btn.style.color=aligning?'#fff':'#000'; }
        function updL(){ if(!lbl) return; lbl.textContent='Add Label: '+(MapApp.labelMode?'ON':'OFF'); lbl.style.background=MapApp.labelMode?'#1a9a4a':'#fff'; lbl.style.color=MapApp.labelMode?'#fff':'#000'; }
        function setAlign(on){ aligning=on;
            if(on){ MapApp.labelMode=false; updL(); MapApp.map.dragging.disable(); }
            else { MapApp.map.dragging.enable(); drag=null; setT(''); }
            updA(); }
        function setLabel(on){ if(!authoring) return; MapApp.labelMode=on;
            if(on){ aligning=false; updA(); MapApp.map.dragging.enable(); drag=null; setT(''); }
            else if(MapApp.areaCapture && MapApp.areaCapture.pending){ finalizeAreaCapture(null); }
            updL(); }
        btn.onclick=()=> setAlign(!aligning);
        if(lbl) lbl.onclick=()=> setLabel(!MapApp.labelMode);
        // Let a read-only backend (serve.py --no-authoring) drop the authoring UI:
        // detectBackend() calls this when /api/ping reports authoring:false.
        MapApp.addLabelBtn = lbl;
        MapApp.disableAuthoringUI = function(){ MapApp.authoring = false; if(lbl){ setLabel(false); lbl.style.display='none'; } };
        // align-mode drag
        MapApp.map.on('mousedown', e=>{ if(!aligning) return;
            drag={ p:MapApp.map.mouseEventToContainerPoint(e.originalEvent), a:e.latlng, last:e.latlng }; });
        MapApp.map.on('mousemove', e=>{ if(!aligning||!drag) return;
            const p=MapApp.map.mouseEventToContainerPoint(e.originalEvent);
            setT(`translate3d(${p.x-drag.p.x}px,${p.y-drag.p.y}px,0)`); drag.last=e.latlng; });
        MapApp.map.on('mouseup', ()=>{ if(!aligning||!drag) return; const d=drag; drag=null; setT('');
            MapApp.align.lat += (d.last.lat - d.a.lat); MapApp.align.lon += (d.last.lng - d.a.lng);
            rerenderAlign(); repositionAreaLabels(); setTimeout(applyTrackOpacity, 600); });
        // label-mode capture: first click = position, second = angle (Esc = horizontal)
        MapApp.map.on('click', e=>{ if(!authoring || !MapApp.labelMode) return; MapApp.map.closePopup();
            if(MapApp.areaCapture && MapApp.areaCapture.pending) finalizeAreaCapture(e.latlng);
            else startAreaCapture(e.latlng); });
    }

'''

_INIT_ORIG = """    function init() {
        // Find the map object (Folium creates it with a specific name)
        const mapContainer = document.querySelector('.folium-map');
        if (!mapContainer) {
            setTimeout(init, 100);
            return;
        }

        // Get map variable name from container id
        const mapId = mapContainer.id;
        MapApp.map = window[mapId];

        if (!MapApp.map || typeof MapApp.map.eachLayer !== 'function') {
            setTimeout(init, 100);
            return;
        }

        console.log('Map initialized, loading manifest...');
        loadManifest();
    }"""

_INIT_DIRECT = """    function init() {
        MapApp.map = window.__run8map;
        console.log('Map initialized, loading manifest...');
        loadManifest();
    }"""

_FETCH_ORIG = """            const response = await fetch(`data/${regionId}.json`);
            const data = await response.json();"""

_FETCH_ALIGN = """            let data;
            if (MapApp.align && MapApp._raw[regionId]) {
                data = JSON.parse(JSON.stringify(MapApp._raw[regionId]));
            } else {
                // Retry transient failures (e.g. a request racing a just-started
                // server) and treat a non-OK HTTP status as an error, so a region
                // is not silently dropped on first load.
                for (let attempt = 1; ; attempt++) {
                    try {
                        const response = await fetch(`data/${regionId}.json`);
                        if (!response.ok) throw new Error(`HTTP ${response.status} fetching ${regionId}.json`);
                        data = await response.json();
                        break;
                    } catch (e) {
                        if (attempt >= 3) throw e;
                        await new Promise(r => setTimeout(r, 300 * attempt));
                    }
                }
                if (MapApp.align) MapApp._raw[regionId] = JSON.parse(JSON.stringify(data));
            }
            if (MapApp.align) MapApp.transformData(data);"""


def generate_align_html(config: VisualizationConfig, output_path: Path, authoring: bool = True) -> None:
    """Generate a standalone (no-Folium) geographic viewer with manual-alignment
    drag. Reuses the geographic viewer's feature code verbatim; the map is created
    directly and the tile-based track is placed via a client-side transform.
    authoring=False hides the "Add Label" button (for hosting to end users)."""
    js = generate_javascript()
    # patch: direct map instead of Folium lookup
    js = js.replace(_INIT_ORIG, _INIT_DIRECT)
    # patch: seed the transform (alignInit) after UI is built, before regions load
    js = js.replace("            setupUI();\n            loadDefaultRegions();",
                    "            setupUI();\n            alignInit();\n            loadDefaultRegions();")
    # patch: cache raw world-coord data + transform on load
    js = js.replace(_FETCH_ORIG, _FETCH_ALIGN)
    # append the align module inside the IIFE, just before the public API
    js = js.replace("    window.MapApp = {\n        openSearch,",
                    ALIGN_JS + "    window.MapApp = {\n        openSearch,")
    # patch: enabling a region pans/zooms the map to it
    js = js.replace(
        "    function toggleRegion(regionId, enabled) {\n"
        "        if (enabled) {\n"
        "            loadRegion(regionId);\n"
        "        } else {\n"
        "            unloadRegion(regionId);\n"
        "        }\n"
        "    }",
        "    async function toggleRegion(regionId, enabled) {\n"
        "        if (enabled) {\n"
        "            await loadRegion(regionId);\n"
        "            if (MapApp.align) { fitToRegion(regionId); applyTrackOpacity(); }\n"
        "        } else {\n"
        "            unloadRegion(regionId);\n"
        "        }\n"
        "    }")

    color_config = generate_color_config(config.colors)
    authoring_js = 'true' if authoring else 'false'
    html = f'''<!DOCTYPE html>
<html><head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>{config.name}</title>
<link rel="icon" href="data:,"/>
<link rel="stylesheet" href="leaflet/leaflet.css"/>
<script src="leaflet/leaflet.js"></script>
<script>window.L||document.write('<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"><\/script>')</script>
<style>html,body{{margin:0;height:100%}}#map{{position:absolute;inset:0}}</style>
</head><body>
<div id="map"></div>
{color_config}
<script>window.TRAIN_STYLE = {{car: {config.train_car_width}, spine: {config.train_spine_width}, carM: {config.train_car_width_m}, labelScaleM: {config.train_label_scale_m}}};</script>
<script>window.__run8_authoring = {authoring_js}; window.__run8map = L.map('map', {{preferCanvas:true, maxZoom:22, zoomControl:true}}).setView([35,-117.8],9);</script>
{js}
</body></html>'''
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"Generated HTML (align): {output_path}")


if __name__ == '__main__':
    import sys
    from output_generator import generate_output

    if len(sys.argv) < 2:
        print("Usage: html_generator.py <config.ini>")
        sys.exit(1)

    from config_parser import parse_config
    config = parse_config(sys.argv[1])

    # Generate JSON files first
    generate_output(config)

    # Generate HTML
    output_path = config.output_dir / "index.html"
    generate_html(config, output_path)
