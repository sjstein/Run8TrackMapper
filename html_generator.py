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
    areaLabel: '{colors.area_label}'
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
            tileBoundaries: false
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
            </style>
            <h3>${MapApp.manifest.name}</h3>
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
        `;
        document.body.appendChild(panel);

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
            {id: 'tileBoundaries', label: 'Tile Boundaries'}
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
            <label>Map Opacity: <input type="range" id="opacity-slider" min="0" max="100" value="100"></label>
        `;
        document.body.appendChild(control);

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
                }
            </style>
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
                tileBoundaries: L.layerGroup()
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

        } catch (error) {
            console.error(`Failed to load region ${regionId}:`, error);
            checkbox.checked = false;
        } finally {
            loader.remove();
            checkbox.disabled = false;
        }
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

        region.visible = true;
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
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
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
    areaLabel: '{colors.area_label}'
}};

(function() {{
    'use strict';

    const COLORS = window.COLORS;

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
        overlayStates: {{ signals: false, industries: false, aiLocations: false, tileBoundaries: false, areaLabels: false }},
        signalLayers: [],
        industryLayers: [],
        aiLayers: [],
        tileLayers: [],
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
                tiles: L.layerGroup()
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

            MapApp.loadedRegions.set(regionId, {{ data, layers, visible: true }});

            // Apply overlay states
            if (MapApp.overlayStates.signals) layers.signals.addTo(MapApp.map);
            if (MapApp.overlayStates.industries) layers.industries.addTo(MapApp.map);
            if (MapApp.overlayStates.aiLocations) layers.aiLocations.addTo(MapApp.map);
            if (MapApp.overlayStates.tileBoundaries) layers.tiles.addTo(MapApp.map);

        }} catch (error) {{
            console.error(`Failed to load region ${{regionId}}:`, error);
        }}
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
        const color = area.color || COLORS.areaLabel;
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
                tileBoundaries: region.layers.tiles
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
