#!/usr/bin/env python3
"""
HTML generator for Run8 Track Mapper.

Generates the standalone manual-alignment index.html (Leaflet + embedded
JavaScript) with dynamic per-region loading.
"""

from pathlib import Path
from typing import List, Tuple, Optional

from config_parser import VisualizationConfig, ColorConfig


def generate_color_config(colors: ColorConfig) -> str:
    """Generate JavaScript color configuration"""
    return f'''<script>
window.COLORS = {{
    track: '{colors.track}',
    trackSelected: '{colors.track_selected}',
    trackHover: '{colors.track_hover}',
    switch: '{colors.switch}',
    switchCtc: '{colors.switch_ctc}',
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
    const TRAIN_STYLE = window.TRAIN_STYLE || {car: 7, spine: 1.5, carM: 3.5, labelScaleM: 30, labelSize: 14, lodScaleM: 300, lodMinCars: 3, lodColor: '#5f6368'};
    // Signal glyph ([signals] config). sizeM is the glyph footprint (m); showIntermediate
    // is the initial state of the Absolute/Intermediate filter. Signals draw at their true
    // .r8 positions (no offset).
    const SIGNAL_STYLE = window.SIGNAL_STYLE || {sizeM: 7.5, showIntermediate: true};

    // Track line width from [track] config: full `width` px at/above `fullZoom`,
    // halving per zoom level below that down to `minWidth` (fullZoom 0 = fixed width).
    const TRACK_STYLE = window.TRACK_STYLE || {width: 5, minWidth: 1.5, fullZoom: 14};
    // Track line weight (px) for the current zoom.
    function trackWeightPx() {
        const max = TRACK_STYLE.width || 5;
        const min = TRACK_STYLE.minWidth || 0;
        const anchor = TRACK_STYLE.fullZoom || 0;
        if (!anchor || !MapApp.map) return max;   // scaling disabled / map not ready
        // Snap the track to its thin min width once zoomed out far enough that trains
        // collapse to a single line, so track + train LOD switch together
        // ([trains] lod_scale_m sets the point; [track] min_width sets the thinness).
        if (typeof _trainCollapsed === 'function' && _trainCollapsed()) return min;
        const w = max * Math.pow(2, MapApp.map.getZoom() - anchor);
        return Math.max(min, Math.min(max, w));
    }
    // Section ids are per-region and COLLIDE across regions, so every section-keyed
    // structure (sectionIndex, selectedSections) is keyed by this region-qualified key
    // instead of the bare id. Section ids are integers, so the '_' join is unambiguous.
    function secKey(regionId, sectionId){ return regionId + '_' + sectionId; }

    // Track colour for a section: CTC (dispatcher-controlled) switches and hand-throw
    // switches get their own colours; non-switch track uses the region's track colour.
    function switchColor(section, regionTrackColor){
        if (!section.is_switch) return regionTrackColor;
        return section.is_ctc_switch ? COLORS.switchCtc : COLORS.switch;
    }
    // Re-weight all (non-selected) track sections for the current zoom.
    function updateTrackWidths() {
        const w = trackWeightPx();
        MapApp.loadedRegions.forEach((region, regionId) => {
            if (!region.layers || !region.layers.sections) return;
            region.layers.sections.eachLayer(group => {
                if (!group.eachLayer) return;
                group.eachLayer(pl => {
                    if (pl._trackLine && pl.setStyle && !MapApp.selectedSections.has(secKey(regionId, pl._sectionId)))
                        pl.setStyle({ weight: w });
                });
            });
        });
    }

    // ========================================
    // MapApp - Main Application State
    // ========================================
    const MapApp = {
        map: null,
        manifest: null,
        loadedRegions: new Map(),  // region_id -> {data, layers, visible}
        sectionIndex: new Map(),   // secKey(region_id, section_id) -> {region_id, polyline, metadata, originalColor}  (section ids collide across regions)
        signalIndex: new Map(),    // secKey(region_id, signal_id) -> {region_id, marker, metadata}  (signal ids collide across regions)
        industryIndex: [],         // [{region_id, data}]
        aiLocationIndex: [],       // [{region_id, data}]
        trainIndex: [],            // [{regionId, trainId, vehicle, layer}] for search
        industrySectionIds: new Set(),  // Set of "regionId_sectionId" keys for industry tracks

        // Local symbol filtering state
        localSymbolIndex: new Set(),    // Set of unique local symbols across all loaded regions
        currentLocalFilter: null,       // Currently selected local symbol (null = ALL)
        industryMarkers: new Map(),     // Map of "regionId_tag" -> {marker, data, regionId} for highlighting

        // Selection state
        selectedSections: new Map(),  // secKey(region_id, section_id) -> {polyline, metadata, region_id}
        currentSelectionRegion: null,

        // Layer groups for overlays
        overlayStates: {
            signals: false,
            industries: false,
            aiLocations: false,
            tileBoundaries: false,
            trains: false,
            grade: false            // Grade heat-map colour mode (#57)
        },
        // Train display options (the "Options" button next to the Trains overlay).
        trainOptions: {
            coloredCars: false,     // false = paint every non-loco car the box-car colour
            showCuts: false,        // false = only consists led by a loco; true = also loose cuts
            showOnlyMoving: false,  // true = only plot trains moving between saves (live only)
            highlightPlayers: false // true = highlight player trains (moving && not AI) with a bright spine
        },

        // Base map layers
        baseLayers: {},  // {name: layer}
        currentBaseLayer: 'None',

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
    // Theme (light / dark)
    // ========================================
    // A single stylesheet defines CSS custom properties for the light theme on
    // :root and overrides them under html[data-theme="dark"]. The light-styled
    // chrome (inline <style> blocks) is left untouched; the dark rules below win
    // by higher specificity (attribute + id). Runtime-built popovers/buttons use
    // the same var() names inline so they follow the theme too. The map area
    // itself is only re-coloured (the None base map / gaps); real OSM/Satellite
    // tiles keep their natural colours.
    function injectThemeStyles() {
        if (document.getElementById('r8-theme-vars')) return;
        const css = `
:root{
  --panel-bg:#ffffff; --panel-fg:#1f1f1f; --text-muted:#666666;
  --border:#dddddd; --border-strong:#888888; --hover-bg:#f5f5f5;
  --kbd-bg:#eeeeee; --map-bg:#e6e6e6; --accent:#007bff; --shadow:rgba(0,0,0,0.2);
  color-scheme:light;
}
html[data-theme="dark"]{
  --panel-bg:#2b2b2b; --panel-fg:#e8e8e8; --text-muted:#a8a8a8;
  --border:#454545; --border-strong:#6a6a6a; --hover-bg:#3a3a3a;
  --kbd-bg:#4a4a4a; --map-bg:#101418; --accent:#4da3ff; --shadow:rgba(0,0,0,0.6);
  color-scheme:dark;
}
.leaflet-container{ background:var(--map-bg); }

html[data-theme="dark"] #control-panel,
html[data-theme="dark"] #search-dialog,
html[data-theme="dark"] #opacity-control,
html[data-theme="dark"] #mouse-position{
  background:var(--panel-bg); color:var(--panel-fg); box-shadow:0 2px 10px var(--shadow);
}
html[data-theme="dark"] #control-panel h3{ border-bottom-color:var(--border); }
html[data-theme="dark"] #control-panel h4{ color:var(--text-muted); }
html[data-theme="dark"] #selection-info{ border-top-color:var(--border); }
html[data-theme="dark"] #control-toggle{ background:var(--kbd-bg); color:var(--panel-fg); }
html[data-theme="dark"] #control-toggle:hover{ background:var(--border-strong); }
html[data-theme="dark"] .search-result{ border-bottom-color:var(--border); }
html[data-theme="dark"] .search-result:hover{ background:var(--hover-bg); }
html[data-theme="dark"] .search-close{ color:var(--text-muted); }
html[data-theme="dark"] #search-dialog input[type="text"],
html[data-theme="dark"] #search-dialog select,
html[data-theme="dark"] #local-symbol-select{
  background:var(--panel-bg); color:var(--panel-fg); border-color:var(--border);
}
html[data-theme="dark"] #train-count,
html[data-theme="dark"] #sim-time{ color:var(--panel-fg); border-bottom-color:var(--border); }

html[data-theme="dark"] .leaflet-popup-content-wrapper,
html[data-theme="dark"] .leaflet-popup-tip{
  background:var(--panel-bg); color:var(--panel-fg); box-shadow:0 3px 14px var(--shadow);
}
html[data-theme="dark"] .leaflet-popup-close-button{ color:var(--text-muted); }
html[data-theme="dark"] .leaflet-bar a,
html[data-theme="dark"] .leaflet-bar a:hover{
  background:var(--panel-bg); color:var(--panel-fg); border-bottom-color:var(--border);
}
html[data-theme="dark"] .leaflet-control-layers,
html[data-theme="dark"] .leaflet-control-attribution{ background:var(--panel-bg); color:var(--text-muted); }
html[data-theme="dark"] .leaflet-control-attribution a{ color:var(--accent); }
html[data-theme="dark"] .leaflet-control-scale-line{
  background:rgba(0,0,0,.5); color:var(--panel-fg); border-color:var(--border-strong); border-top:none;
}
`;
        const el = document.createElement('style');
        el.id = 'r8-theme-vars';
        el.textContent = css;
        document.head.appendChild(el);
    }
    function currentTheme() {
        return document.documentElement.getAttribute('data-theme') || 'light';
    }
    function applyTheme(mode) {
        document.documentElement.setAttribute('data-theme', mode);
        const cb = document.getElementById('theme-toggle');
        if (cb) cb.checked = (mode === 'dark');
        // Signal glyphs are canvas-drawn, so their outline colour must be re-baked when
        // the theme flips (light black <-> dark light-grey). Guarded for early calls.
        if (typeof rerenderSignals === 'function' && MapApp.loadedRegions && MapApp.loadedRegions.size)
            rerenderSignals();
    }
    function setTheme(mode) {
        applyTheme(mode);
        try { localStorage.setItem('run8_theme', mode); } catch (e) {}
    }
    // Initial theme: an explicit saved choice wins; otherwise follow the OS
    // preference (and keep following it until the user picks one explicitly).
    function initTheme() {
        injectThemeStyles();
        let saved = null;
        try { saved = localStorage.getItem('run8_theme'); } catch (e) {}
        const prefersDark = !!(window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches);
        applyTheme((saved === 'dark' || saved === 'light') ? saved : (prefersDark ? 'dark' : 'light'));
        if (window.matchMedia) {
            try {
                window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', (e) => {
                    let s = null; try { s = localStorage.getItem('run8_theme'); } catch (_) {}
                    if (s !== 'dark' && s !== 'light') applyTheme(e.matches ? 'dark' : 'light');
                });
            } catch (e) {}
        }
    }

    // ========================================
    // UI Setup
    // ========================================
    function setupUI() {
        initTheme();
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

        // Start with no base map ("None"); the user can switch via the radios.
        MapApp.baseLayers['None'].addTo(MapApp.map);
        MapApp.currentBaseLayer = 'None';

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
            <div class="overlay-item" style="margin:2px 0 4px 0;">
                <input type="checkbox" id="theme-toggle">
                <label for="theme-toggle">Dark mode</label>
            </div>
            <h4>Base Map</h4>
            <div id="basemap-list">
                <div class="basemap-item">
                    <input type="radio" name="basemap" id="basemap-osm" value="OpenStreetMap">
                    <label for="basemap-osm">OpenStreetMap</label>
                </div>
                <div class="basemap-item">
                    <input type="radio" name="basemap" id="basemap-satellite" value="Satellite">
                    <label for="basemap-satellite">Satellite</label>
                </div>
                <div class="basemap-item">
                    <input type="radio" name="basemap" id="basemap-none" value="None" checked>
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

        // Dark-mode toggle (reflects the theme initTheme() already applied).
        const themeToggle = document.getElementById('theme-toggle');
        if (themeToggle) {
            themeToggle.checked = (currentTheme() === 'dark');
            themeToggle.addEventListener('change', (e) => setTheme(e.target.checked ? 'dark' : 'light'));
        }

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
        // Grade heat-map colour mode (#57): a track-colouring toggle, shown only when the
        // build carries grade config. Its default-on state comes from [grade] show_by_default.
        if (MapApp.manifest && MapApp.manifest.grade) {
            overlays.push({id: 'grade', label: 'Grade'});
            MapApp.overlayStates.grade = !!MapApp.manifest.grade.show_by_default;
        }
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

            // Signals: a "Filter" button (Absolute / Intermediate) beside the row,
            // mirroring the Area Labels filter. Absolutes are the dispatcher-relevant
            // signals; hiding intermediates de-clutters.
            if (overlay.id === 'signals') addSignalFilterButton(item);
        }
        updateGradeLegend();   // show the grade legend now if grade mode starts on (#57)

        // Setup local symbol filter dropdown
        document.getElementById('local-symbol-select').addEventListener('change', (e) => {
            MapApp.currentLocalFilter = e.target.value || null;
            applyLocalSymbolHighlighting();
            // Picking a specific Local: make industries visible and frame the ones it
            // works, so a user learning territory sees that Local's whole footprint.
            if (MapApp.currentLocalFilter) {
                if (!MapApp.overlayStates.industries) {
                    const cb = document.getElementById('overlay-industries');
                    if (cb) cb.checked = true;
                    toggleOverlay('industries', true);
                }
                zoomToLocal(MapApp.currentLocalFilter);
            }
        });

        // Zoom-scale the track line width (thinner when zoomed out).
        MapApp.map.on('zoomend', updateTrackWidths);
        // Re-weight rail-vehicle bodies so they stay wider than the track at any zoom.
        MapApp.map.on('zoomend', updateTrainWidths);
        // Show/hide per-RV destination labels by zoom (visible at ~20 m scale or tighter).
        MapApp.map.on('zoomend', updateTrainLabelVisibility);
        // Panning changes which tags fall in the viewport, so re-cull on move end too (#76).
        MapApp.map.on('moveend', updateTrainLabelVisibility);
        // Switch train detail level (full cars <-> single collapsed line) by zoom.
        MapApp.map.on('zoomend', updateTrainLOD);
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
                <option value="area">Area Label</option>
                <option value="industry">Industry</option>
                <option value="signal">Signal</option>
                <option value="tile">Tile coord</option>
                <option value="section">Track Section</option>
                <option value="train">Train / Rail Vehicle</option>
            </select>
            <div id="train-field-row" style="display:none;margin:6px 0;font-size:13px;">
                <span style="color:var(--text-muted);">Match:</span>
                <label style="margin-left:4px;"><input type="radio" name="train-field" value="all" checked> All</label>
                <label style="margin-left:6px;"><input type="radio" name="train-field" value="unit"> Unit&nbsp;#</label>
                <label style="margin-left:6px;"><input type="radio" name="train-field" value="tag"> Tag</label>
                <label style="margin-left:6px;"><input type="radio" name="train-field" value="trainId"> Train&nbsp;ID</label>
            </div>
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
            if (e.key === 'Escape') { closeSearch(); return; }
            // Tile-coord search yields a single result, so Enter jumps straight to it
            // (render synchronously to beat the input debounce, then activate the row).
            if (e.key === 'Enter' && document.getElementById('search-type').value === 'tile') {
                performSearch();
                const first = document.querySelector('#search-results .search-result');
                if (first) first.click();
            }
        });

        // The Train / Rail Vehicle search can be narrowed to one field via radios
        // (All / Unit # / Tag / Train ID), shown only for that search type.
        const typeSel = document.getElementById('search-type');
        const trainFieldRow = document.getElementById('train-field-row');
        const syncTrainFieldRow = () => {
            trainFieldRow.style.display = (typeSel.value === 'train') ? 'block' : 'none';
        };
        // The tile-coord search takes a numeric coord, so hint the format in the box.
        const syncSearchPlaceholder = () => {
            input.placeholder = (typeSel.value === 'tile')
                ? 'tile_x, tile_z   (e.g. 209, -10)'
                : 'Enter search term...';
        };
        typeSel.addEventListener('change', () => {
            syncTrainFieldRow(); syncSearchPlaceholder(); performSearch();
        });
        trainFieldRow.querySelectorAll('input[name="train-field"]')
            .forEach(r => r.addEventListener('change', performSearch));
        syncTrainFieldRow();
        syncSearchPlaceholder();

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
                    /* Size the panel to its widest row so the background always
                       encloses the sliders (an absolutely-positioned shrink-to-fit
                       box otherwise under-sizes flex rows in some browsers, letting
                       the sliders overrun the right edge). */
                    width: max-content;
                    background: white;
                    padding: 10px;
                    border-radius: 4px;
                    box-shadow: 0 2px 6px rgba(0,0,0,0.2);
                    z-index: 1000;
                    font-size: 12px;
                }
                /* Each row: fixed-width text column + equal-length slider, vertically
                   centered, so all sliders line up in one column. */
                #opacity-control label {
                    display: flex;
                    align-items: center;
                    gap: 8px;
                    margin-top: 4px;
                    /* Explicit row width (= label 84 + gap 8 + slider 110) so the
                       panel's content width is deterministic. Firefox ignores a
                       range input's flex-basis when measuring max-content, which
                       otherwise sizes the panel to the text alone and lets the
                       slider overrun the right edge. */
                    width: 202px;
                }
                #opacity-control label:first-of-type { margin-top: 0; }
                #opacity-control .oc-lbl { flex: 0 0 84px; white-space: nowrap; }
                #opacity-control input[type=range] {
                    width: 110px;
                    flex: 0 0 110px;
                    margin: 0;
                }
            </style>
            <label><span class="oc-lbl">Map Opacity:</span><input type="range" id="opacity-slider" min="0" max="100" value="${Math.round(mapOpacity * 100)}"></label>
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
                #sim-time {
                    display: none;
                    margin-bottom: 3px;
                    padding-bottom: 3px;
                    border-bottom: 1px solid #eee;
                    color: #333;
                }
                #mouse-coords { display: block; }
            </style>
            <span id="train-count"></span>
            <span id="sim-time"></span>
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
                trainLabels: L.layerGroup(),     // master: ALL per-RV tags; never added to the map directly
                trainLabelsView: L.layerGroup()  // on-map subset, culled to the viewport (#76)
            };

            // Get region-specific track color (from manifest) or fall back to global default
            const regionManifest = MapApp.manifest.regions.find(r => r.id === regionId);
            const regionTrackColor = regionManifest?.track_color || COLORS.track;

            // Render sections
            for (const section of data.sections) {
                // Each section may have multiple paths (especially switches)
                const sectionGroup = L.featureGroup();

                for (const path of section.paths) {
                    const trackColor = switchColor(section, regionTrackColor);
                    const polyline = L.polyline(path, {
                        color: trackColor,
                        weight: trackWeightPx(),   // zoom-scaled (updateTrackWidths on zoomend)
                        opacity: 0.8
                    });
                    polyline._trackLine = true;
                    polyline._sectionId = section.id;
                    polyline._origColor = trackColor;   // per-polyline normal colour (region/switch aware)
                    polyline._gradePct = section.grade_pct || 0;   // for the Grade colour mode (#57)

                    // Build detailed section popup
                    const sectionType = section.is_switch
                        ? (section.is_ctc_switch ? ' (CTC Switch)' : ' (Hand-throw Switch)') : '';
                    let sectionPopup = `<b>Section ${section.id}${sectionType}</b><br>`;
                    sectionPopup += `Length: ${section.length_ft.toFixed(1)} ft (${section.length_m.toFixed(1)} m)<br>`;
                    sectionPopup += `Grade: ${gradeText(section.grade_pct)}<br>`;
                    sectionPopup += `Paths: ${section.paths.length}`;

                    polyline.bindPopup(sectionPopup, {maxWidth: 250});
                    polyline.bindTooltip(`Section ${section.id} · grade ${gradeText(section.grade_pct)}`, {sticky: true});

                    // Hover highlight handlers
                    polyline.on('mouseover', function() {
                        // Don't change if section is selected
                        if (!MapApp.selectedSections.has(secKey(regionId, section.id))) {
                            this.setStyle({ color: COLORS.trackHover });
                        }
                    });
                    polyline.on('mouseout', function() {
                        // Restore resting color if not selected (grade mode > industry > normal).
                        if (!MapApp.selectedSections.has(secKey(regionId, section.id))) {
                            this.setStyle({ color: sectionRestColor(regionId, this) });
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
                const originalColor = switchColor(section, regionTrackColor);
                MapApp.sectionIndex.set(secKey(regionId, section.id), {region_id: regionId, polyline: sectionGroup, metadata: section, originalColor: originalColor});
            }

            // Signals: dispatcher-style glyphs drawn at their true .r8 positions (see renderSignals).
            renderSignals(regionId, data, layers);

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
                if (MapApp.currentLocalFilter) applyLocalSymbolHighlighting();  // red/grey labels
            }
            if (MapApp.overlayStates.aiLocations) layers.aiLocations.addTo(MapApp.map);
            if (MapApp.overlayStates.tileBoundaries) layers.tileBoundaries.addTo(MapApp.map);
            if (MapApp.overlayStates.trains) layers.trains.addTo(MapApp.map);
            if (MapApp.overlayStates.grade) refreshAllTrackColors();  // paint this region by grade (#57)
            updateTrainLabelVisibility();
            // Area labels are gated by the visible regions' tiles - re-evaluate now
            // that this region's tiles are available.
            if (typeof updateAreaLabelRegionVisibility === 'function') updateAreaLabelRegionVisibility();

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
    const PLAYER_HL_COLOR = '#00e5ff';   // bright cyan spine for player-crewed trains
    const COLLAPSED_BODY_COLOR = (window.TRAIN_STYLE && TRAIN_STYLE.lodColor) || '#5f6368';  // collapsed train line colour ([trains] lod_color)
                                             // (the head arrow carries the railroad colour)
    function _isLocoType(unitType) {
        return /DieselEngine|Electric|Steam|Engine/i.test(unitType || '');
    }
    // A "train" is a consist whose lead (first) vehicle is a locomotive; a cut of
    // cars with no lead loco is a "non-train" (hidden by the Hide non-trains option).
    function _isConsist(train) {
        const lead = train && train.vehicles && train.vehicles[0];
        return !!(lead && _isLocoType(lead.unit_type));
    }
    // A "player" train (heuristic): MOVING and NOT AI-crewed (TrainWasAI false). The
    // world save has no crew field, so this is the best proxy - a moving non-AI train
    // is almost certainly under a player. `moving` is computed server-side by diffing
    // consecutive saves (serve.py); absent (static output) => never a player here.
    function _isPlayerTrain(train) {
        return !!(train.moving && train.was_ai === false);
    }
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
        // "Colored Cars" off: paint every non-loco car the box-car colour.
        if (MapApp.trainOptions && !MapApp.trainOptions.coloredCars)
            return carTypeColor('Box_Car') || COLORS.train;
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
                    // Player-highlight spine stays a touch wider than the car so it
                    // reads as a coloured casing at every zoom.
                    else if (l._rvHighlight && l.setStyle) l.setStyle({ weight: _highlightWeight(w) });
                    // Loco arrow polygons: rebuilt so their width tracks the car width.
                    else if (l._locoArrow && l.setLatLngs) l.setLatLngs(locoArrowLatLngs(l._body, l._front0));
                    // Head-end arrows keep a constant on-screen size + stable heading across zoom.
                    else if (l._rvArrow && l.setLatLngs) l.setLatLngs(_arrowLatLngs(l._headTip, l._headRefs));
                });
        });
    }
    function _highlightWeight(carWeight) { return Math.max(carWeight * 1.6, 4); }

    // Centered destination-tag label for one RV (shown only when zoomed in).
    function trainDestLabelIcon(text) {
        // Honour the global "Text Size" slider (#82): base tag size times the multiplier.
        const px = ((TRAIN_STYLE.labelSize || 14) * (MapApp.areaFontScale || 1)).toFixed(1);
        return L.divIcon({
            className: 'rv-dest-label',
            html: `<div style="transform:translate(-50%,-50%);color:#fff;`
                + `font:bold ${px}px/1 system-ui,sans-serif;white-space:nowrap;`
                + `text-shadow:-1px -1px 0 #000,1px -1px 0 #000,-1px 1px 0 #000,1px 1px 0 #000;">`
                + `${text}</div>`,
            iconSize: null, iconAnchor: [0, 0]
        });
    }
    // Re-set every rail-vehicle destination tag's icon so it picks up the current
    // "Text Size" multiplier (#82). The marker keeps its own text in _destText.
    function updateTrainLabelSizes() {
        if (!MapApp.loadedRegions) return;
        MapApp.loadedRegions.forEach(region => {
            const lyr = region.layers;
            if (!lyr || !lyr.trainLabels) return;
            lyr.trainLabels.eachLayer(m => {
                if (m._destText != null) m.setIcon(trainDestLabelIcon(m._destText));
            });
        });
    }

    // Draw each rail vehicle as a body polyline spanning its two trucks. Body
    // points are already [lat, lon] at render time (geographic mode stores them
    // that way; align mode converts them in transformData), so no coord swap.
    // `layers` carries .trains (bodies + spine) and .trainLabels (destination tags).
    function renderTrains(regionId, data, layers) {
        // Idempotent per region: drop prior entries (e.g. a re-align rebuild).
        MapApp.trainIndex = MapApp.trainIndex.filter(it => it.regionId !== regionId);

        const opts = MapApp.trainOptions || {};

        // Resolve every train's drawable cars once (honouring the Train Options).
        const drawn = [];
        for (const train of (data.trains || [])) {
            const isConsist = _isConsist(train);
            if (!isConsist && !opts.showCuts) continue;         // cuts (no lead loco) hidden unless shown
            if (opts.showOnlyMoving && !train.moving) continue; // hide stationary trains
            const cars = [];
            for (const v of train.vehicles) {
                if (!v.resolved || !v.body || v.body.length < 2) continue;
                cars.push({v, isLoco: _isLocoType(v.unit_type)});
            }
            if (!cars.length) continue;
            const highlight = !!(opts.highlightPlayers && _isPlayerTrain(train));
            drawn.push({train, cars, highlight});
        }

        // Zoom LOD: when zoomed out past the threshold (scale bar >= lodScaleM,
        // default 300 m), drop singles / short trains and draw each longer train
        // (> lodMinCars cars, default 3) as ONE solid line in its lead car's colour
        // that just shows the train's length. Thresholds live in TRAIN_STYLE (moved
        // to config after review; fall back to 300 / 3 here).
        MapApp._trainLOD = _trainCollapsed() ? 'collapsed' : 'detailed';
        if (MapApp._trainLOD === 'collapsed') {
            const minCars = (window.TRAIN_STYLE && TRAIN_STYLE.lodMinCars) || 3;
            for (const {train, cars, highlight} of drawn) {
                if (cars.length > minCars) drawCollapsedTrain(regionId, train, cars, layers.trains, highlight);
            }
            return;
        }

        // Pass 1: every train's connecting spine FIRST, so the RV bodies drawn in
        // pass 2 sit ON TOP of it (the spine reads as a thin backbone behind the
        // cars instead of a line painted across them). Player trains get a bright spine.
        for (const {cars, highlight} of drawn) drawTrainOutline(cars.map(c => c.v.body), layers.trains, highlight);

        // Pass 2: RV bodies + destination tags, above the spines. A locomotive is one
        // arrow polygon (nose = facing); a car is a plain blunt body line.
        for (const {train, cars} of drawn) {
            for (const {v, isLoco} of cars) {
                let layer;
                if (isLoco) {
                    const frontAt0 = (v.front0 !== false) !== LOCO_FACING_FLIP;   // default front0=true
                    const color = rvBodyColor(v, true);
                    layer = L.polygon(locoArrowLatLngs(v.body, frontAt0), {
                        color: color, fillColor: color, fillOpacity: 0.95,
                        weight: 1, opacity: 0.95
                    });
                    layer._locoArrow = true; layer._body = v.body; layer._front0 = frontAt0;  // rebuilt on zoom
                } else {
                    layer = L.polyline(v.body, {
                        color: rvBodyColor(v, false),
                        weight: rvBodyWeightPx(),
                        opacity: 0.95,
                        lineCap: 'butt'
                    });
                    layer._rvBody = true;   // marks it for zoom re-weighting
                }
                layer.bindTooltip(trainVehicleTooltip(train, v), {sticky: true});
                layer.bindPopup(trainVehiclePopup(train, v), {maxWidth: 300});
                layer.addTo(layers.trains);
                MapApp.trainIndex.push({regionId, trainId: train.train_id, vehicle: v, layer});

                // Destination tag centered on the car (zoom-gated visibility).
                if (v.destination_tag) {
                    const lbl = L.marker(_midpoint(v.body), {
                        icon: trainDestLabelIcon(v.destination_tag),
                        interactive: false, keyboard: false
                    });
                    lbl._destText = v.destination_tag;   // kept so updateTrainLabelSizes() can re-scale it (#82)
                    lbl.addTo(layers.trainLabels);
                }
            }
        }
    }

    // ---- Zoom LOD: collapse long trains to a single line when zoomed out ----
    // True when the scale bar reads >= lodScaleM metres (default 300) - i.e. far out.
    function _trainCollapsed() {
        const thr = (window.TRAIN_STYLE && TRAIN_STYLE.lodScaleM) || 300;
        return _scaleBarMeters() >= thr;
    }
    // Triangle (in [lat,lon]) for a head-end arrow, computed in PIXEL space for a
    // constant on-screen size. Heading = from a reference point back in the consist
    // toward the head tip. `refsLL` are the car centres head->tail: we walk them to
    // the first that is >= HEADING_MIN_PX from the tip, so the direction stays stable
    // even zoomed out (a single loco is sub-pixel then, which made a near-only heading
    // swing wildly). Falls back to the farthest ref. Re-fitted on zoom by updateTrainWidths.
    function _arrowLatLngs(tipLL, refsLL) {
        const map = MapApp.map;
        const HEADING_MIN_PX = 14;
        const tp = map.latLngToLayerPoint(tipLL);
        let rp = null;
        for (const ll of (refsLL || [])) {
            const p = map.latLngToLayerPoint(ll);
            if (Math.hypot(p.x - tp.x, p.y - tp.y) >= HEADING_MIN_PX) { rp = p; break; }
        }
        if (!rp && refsLL && refsLL.length) rp = map.latLngToLayerPoint(refsLL[refsLL.length - 1]);
        if (!rp) return [tipLL, tipLL, tipLL];   // degenerate; nothing sensible to point at
        let dx = tp.x - rp.x, dy = tp.y - rp.y;
        const len = Math.hypot(dx, dy) || 1; dx /= len; dy /= len;   // heading unit (px)
        const nx = -dy, ny = dx;                                     // perpendicular
        const AHEAD = 3, BASE = 11, HALF = 6;                        // arrowhead px size
        const pts = [
            [tp.x + dx * AHEAD,          tp.y + dy * AHEAD],          // apex (ahead of tip)
            [tp.x - dx * BASE + nx * HALF, tp.y - dy * BASE + ny * HALF],
            [tp.x - dx * BASE - nx * HALF, tp.y - dy * BASE - ny * HALF]
        ];
        return pts.map(p => map.layerPointToLatLng(L.point(p[0], p[1])));
    }
    // Which way a locomotive faces. The server resolves the world-save
    // reverseDirection + raw truck geometry into `front0` (True => the loco's front
    // is body[0]); see region_extractor._loco_front_at_zero. LOCO_FACING_FLIP inverts
    // every loco at once if they ever read backwards against the sim (viewer-side
    // calibration; no re-extract).
    const LOCO_FACING_FLIP = false;
    // A locomotive is drawn as ONE arrow polygon (rectangle body + pointed nose at its
    // front end) instead of a car's plain body line, so the powered unit and its facing
    // read at a glance. Built in PIXEL space so its width matches the car bodies
    // (rvBodyWeightPx) and it is rebuilt on zoom (updateTrainWidths), while its length
    // stays geographic (the body endpoints). `body` = the loco's [lat,lon] body points;
    // `frontAt0` = front is body[0].
    function locoArrowLatLngs(body, frontAt0) {
        const map = MapApp.map;
        const front = frontAt0 ? body[0] : body[body.length - 1];
        const back  = frontAt0 ? body[body.length - 1] : body[0];
        const fp0 = map.latLngToLayerPoint(front), bp0 = map.latLngToLayerPoint(back);
        let dx = fp0.x - bp0.x, dy = fp0.y - bp0.y;            // back -> front
        const len0 = Math.hypot(dx, dy) || 1; dx /= len0; dy /= len0;
        const nx = -dy, ny = dx;                              // perpendicular (px)
        const half = rvBodyWeightPx() / 2;                    // match the car body width
        // Draw exactly to the body endpoints (no end extension): the body is now the
        // real length (RV_LENGTH minus a small fixed coupler gap), so coupled locos
        // already sit the correct gap apart like cars. Nose is a capped fraction of the
        // body length so it stays a sensible arrowhead on wide (zoomed-in) bodies.
        const span = Math.hypot(fp0.x - bp0.x, fp0.y - bp0.y) || 1;
        const nose = Math.min(1.8 * half, 0.33 * span);
        const rx = fp0.x - dx * nose, ry = fp0.y - dy * nose;  // rectangle/nose junction
        const pts = [
            [bp0.x + nx * half, bp0.y + ny * half],           // back, left
            [rx + nx * half, ry + ny * half],                 // nose base, left
            [fp0.x, fp0.y],                                   // nose tip (front)
            [rx - nx * half, ry - ny * half],                 // nose base, right
            [bp0.x - nx * half, bp0.y - ny * half]            // back, right
        ];
        return pts.map(p => map.layerPointToLatLng(L.point(p[0], p[1])));
    }
    // Draw one train as a single line tracing its length. Zoomed out, the head end
    // matters most: the body is a neutral grey and the LEAD locomotive gets a small
    // arrow in the railroad's colour pointing in the direction of travel. (A
    // highlighted player train stays fully cyan.) One trainIndex entry (lead vehicle)
    // keeps search / follow working while collapsed.
    function drawCollapsedTrain(regionId, train, cars, layerGroup, highlight) {
        const bodies = cars.map(c => c.v.body);
        const centers = bodies.map(_midpoint);
        const order = _chainOrder(centers);
        const cen = order.map(i => centers[i]);
        const ob = order.map(i => bodies[i]);
        const frontEnd = _outerEnd(ob[0], cen.length > 1 ? cen[1] : null);
        const rearEnd = _outerEnd(ob[ob.length - 1], cen.length > 1 ? cen[cen.length - 2] : null);
        const lead = cars[0];
        const railColor = rvBodyColor(lead.v, lead.isLoco);   // railroad leader colour

        const line = L.polyline([frontEnd, ...cen, rearEnd], {
            color: highlight ? PLAYER_HL_COLOR : COLLAPSED_BODY_COLOR,
            weight: rvBodyWeightPx(),
            opacity: 0.95,
            lineCap: 'round'
        });
        line._rvBody = true;   // rescales with zoom like a normal RV body
        line.bindTooltip(trainVehicleTooltip(train, lead.v), {sticky: true});
        line.bindPopup(trainVehiclePopup(train, lead.v), {maxWidth: 300});
        line.addTo(layerGroup);
        MapApp.trainIndex.push({regionId, trainId: train.train_id, vehicle: lead.v, layer: line});

        // Head arrow at the lead loco's outer tip, pointing the way it faces. Heading
        // is taken from the car centres head->tail (`centers`, in consist order), which
        // stay well-separated in pixels at any zoom - not from the loco's own tiny body.
        // Only draw it when the head (cars[0]) is actually a locomotive: a loose cut of
        // cars shown via "Show cuts of cars" has no lead loco and gets no arrow.
        const lb = lead.v.body;
        if (lead.isLoco && lb && lb.length >= 2) {
            const nb = cars.length > 1 ? _midpoint(cars[1].v.body) : _midpoint(cen);
            const e0 = lb[0], eN = lb[lb.length - 1];
            const tip = _distLL(e0, nb) >= _distLL(eN, nb) ? e0 : eN;   // end away from the train
            const arrowColor = highlight ? PLAYER_HL_COLOR : railColor;
            const arrow = L.polygon(_arrowLatLngs(tip, centers), {
                color: arrowColor, fillColor: arrowColor, fillOpacity: 1,
                weight: 1, opacity: 1, interactive: false
            });
            arrow._rvArrow = true;
            arrow._headTip = tip; arrow._headRefs = centers;
            arrow.addTo(layerGroup);
        }
    }
    // Re-render all loaded regions' trains when a zoom change crosses the LOD
    // threshold (detailed <-> collapsed), reusing each region's stored data.
    function updateTrainLOD() {
        const mode = _trainCollapsed() ? 'collapsed' : 'detailed';
        if (mode === MapApp._trainLOD) return;
        MapApp.loadedRegions.forEach((region, regionId) => {
            if (!region.layers || !region.layers.trains) return;
            region.layers.trains.clearLayers();
            region.layers.trainLabels.clearLayers();
            renderTrains(regionId, region.data, region.layers);
            if (MapApp.overlayStates.trains) region.layers.trains.addTo(MapApp.map);
        });
        updateTrainLabelVisibility();
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
        // Cull to the viewport (#76): with tens of thousands of destination tags,
        // putting them all on the map at once is slow. The master `trainLabels` group
        // holds every tag off-map; here we rebuild an on-map `trainLabelsView` group
        // containing only the tags whose position is within a slightly padded view.
        // Rebuilding view-first also drops any stale tags left by a train re-render.
        const bounds = show ? MapApp.map.getBounds().pad(0.2) : null;
        MapApp.loadedRegions.forEach(region => {
            const lyr = region.layers;
            if (!lyr || !lyr.trainLabels || !lyr.trainLabelsView) return;
            lyr.trainLabelsView.clearLayers();
            if (show && region.visible) {
                lyr.trainLabels.eachLayer(m => {
                    if (bounds.contains(m.getLatLng())) lyr.trainLabelsView.addLayer(m);
                });
                if (!MapApp.map.hasLayer(lyr.trainLabelsView)) lyr.trainLabelsView.addTo(MapApp.map);
            } else if (MapApp.map.hasLayer(lyr.trainLabelsView)) {
                MapApp.map.removeLayer(lyr.trainLabelsView);
            }
        });
        updateTrainCount();
    }
    // ---- Signal glyph rendering ----
    // Signals are drawn at their TRUE .r8 mast position (Run8 is ground truth): the raw
    // position is already offset to the correct side of the governed track, so there is no
    // artificial offset, side-guessing, or manual placement.
    // Mast/head outline colour. Black (config signal_border_single) reads well on the
    // light map but disappears in night mode, so use a light stroke when the theme is
    // dark. Canvas colours are baked at draw time -> applyTheme re-renders on a change.
    function signalOutlineColor() {
        return (typeof currentTheme === 'function' && currentTheme() === 'dark')
            ? '#e8e8e8' : COLORS.signalBorderSingle;
    }
    // Glyph geometry (metres) at the signal's true position: head centre [lat,lon], the
    // mast+crossbar multi-segment latlngs, and the head radius. The crossbar(s) point the
    // facing direction (rotation + 180, same as the old triangle tip); stacked heads add
    // extra shorter crossbars.
    function signalGlyphGeom(signal) {
        const s = SIGNAL_STYLE.sizeM || 7.5, headR = 0.34 * s;
        const rot = (signal.rotation * Math.PI / 180) + Math.PI;
        const cosLat = Math.cos(signal.lat * Math.PI / 180) || 1e-6;
        const M2LAT = 1 / 111320, M2LON = 1 / (111320 * cosLat);
        const fE = Math.sin(rot), fN = Math.cos(rot);
        const pt = (F) => [signal.lat + F * fN * M2LAT, signal.lon + F * fE * M2LON];
        const perp = (F, half) => {   // crossbar endpoints: F forward, +/-half lateral
            const rE = Math.cos(rot), rN = -Math.sin(rot);
            return [[signal.lat + (F * fN - half * rN) * M2LAT, signal.lon + (F * fE - half * rE) * M2LON],
                    [signal.lat + (F * fN + half * rN) * M2LAT, signal.lon + (F * fE + half * rE) * M2LON]];
        };
        const heads = (signal.stacked_ids && signal.stacked_ids.length > 1) ? signal.stacked_ids.length : 1;
        const headC = pt(0);
        const segs = [[pt(headR), pt(s)]];        // stem (circle edge -> tip)
        for (let i = 0; i < heads; i++) {         // head 1 full at tip; extras shorter, set back
            const half = 0.55 * s * (1 - 0.30 * i);
            segs.push(perp(s - 0.28 * s * i, half));
        }
        return { headC, segs, headR };
    }
    function signalInfoHtml(signal) {
        let h = `<b>${signal.name}</b><br>`;
        h += `Model: ${signal.model_name}<br>`;
        h += `Type: ${signal.type === 'absolute' ? 'Absolute' : 'Intermediate'}<br>`;
        h += `Dwarf: ${signal.is_dwarf ? 'Yes' : 'No'}<br>`;
        h += `Switch Indicator: ${signal.is_switch_indicator ? 'Yes' : 'No'}<br>`;
        h += `Advance Diverging: ${signal.is_advance_diverging ? 'Yes' : 'No'}`;
        return h;
    }
    // (Re)draw one region's signals from its data + current type filter.
    // Safe to call repeatedly (clears the region's signal layers/index first).
    function renderSignals(regionId, data, layers) {
        for (const [k, d] of MapApp.signalIndex) if (d.region_id === regionId) MapApp.signalIndex.delete(k);
        layers.signals.clearLayers();
        if (!MapApp.signalTypeVisible)
            MapApp.signalTypeVisible = { absolute: true, intermediate: SIGNAL_STYLE.showIntermediate !== false };
        const tv = MapApp.signalTypeVisible;
        const border = signalOutlineColor();
        // Dedupe physical stacks: the extractor emits one record carrying all member ids
        // PLUS a solo record per member, so draw one representative per stack and drop the
        // solo members (their ids still map to the rep's marker below for search).
        const stackMemberIds = new Set();
        for (const sg of (data.signals || []))
            if (sg.stacked_ids && sg.stacked_ids.length > 1)
                for (const m of sg.stacked_ids) stackMemberIds.add(m);
        const seen = new Set(), reps = [];
        for (const sg of (data.signals || [])) {
            const st = (sg.stacked_ids && sg.stacked_ids.length) ? sg.stacked_ids : [sg.id];
            if (st.length > 1) {
                const key = st.slice().sort((a, b) => a - b).join(',');
                if (seen.has(key)) continue;
                seen.add(key); reps.push(sg);
            } else if (!stackMemberIds.has(sg.id)) {
                reps.push(sg);
            }
        }
        // Type filter (absolute always on; intermediate toggled by the Signals filter).
        const drawSignals = reps.filter(sg => tv[sg.type] !== false);
        for (const signal of drawSignals) {
            const fillColor = signal.type === 'absolute' ? COLORS.signalAbsolute : COLORS.signalIntermediate;
            const g = signalGlyphGeom(signal);
            const mast = L.polyline(g.segs, { color: border, weight: 3, lineCap: 'round' });
            const head = L.circle(g.headC, { radius: g.headR, color: border, weight: 2,
                fillColor: fillColor, fillOpacity: 0.9 });
            const marker = L.featureGroup([mast, head]);
            const tooltipText = (signal.stacked_ids && signal.stacked_ids.length > 1)
                ? `Signals ${signal.stacked_ids.join(', ')}` : signal.name;
            marker.bindTooltip(tooltipText, { sticky: true });
            marker.bindPopup(signalInfoHtml(signal), { maxWidth: 300 });
            layers.signals.addLayer(marker);
            const idxIds = (signal.stacked_ids && signal.stacked_ids.length) ? signal.stacked_ids : [signal.id];
            for (const mid of idxIds)
                MapApp.signalIndex.set(secKey(regionId, mid), { region_id: regionId, marker, metadata: signal });
        }
    }
    function rerenderRegionSignals(regionId) {
        const region = MapApp.loadedRegions.get(regionId);
        if (!region || !region.layers || !region.layers.signals) return;
        renderSignals(regionId, region.data, region.layers);
        if (MapApp.overlayStates.signals) region.layers.signals.addTo(MapApp.map);
    }
    function rerenderSignals() {
        for (const [id] of MapApp.loadedRegions) rerenderRegionSignals(id);
    }
    // A "Filter" button beside the Signals row that opens the Absolute/Intermediate
    // popover (mirrors the Area Labels filter button; keeps the overlay panel tidy).
    function addSignalFilterButton(item) {
        if (!MapApp.signalTypeVisible)
            MapApp.signalTypeVisible = { absolute: true, intermediate: SIGNAL_STYLE.showIntermediate !== false };
        const btn = document.createElement('button');
        btn.id = 'signal-filter-btn'; btn.type = 'button';
        btn.title = 'Choose which signal types to show';
        btn.textContent = 'Filter';
        btn.style.cssText = 'margin-left:auto;font-size:11px;padding:1px 7px;cursor:pointer;';
        item.appendChild(btn);
        btn.addEventListener('click', (e) => { e.stopPropagation(); toggleSignalFilterPopover(e.currentTarget); });
    }
    function closeSignalFilterPopover() {
        const pop = document.getElementById('signal-filter-popover');
        if (pop) pop.remove();
        document.removeEventListener('mousedown', signalFilterAway, true);
    }
    function signalFilterAway(e) {
        const pop = document.getElementById('signal-filter-popover');
        if (pop && !pop.contains(e.target) && e.target.id !== 'signal-filter-btn') closeSignalFilterPopover();
    }
    function toggleSignalFilterPopover(anchorBtn) {
        if (document.getElementById('signal-filter-popover')) { closeSignalFilterPopover(); return; }
        const tv = MapApp.signalTypeVisible || (MapApp.signalTypeVisible =
            { absolute: true, intermediate: SIGNAL_STYLE.showIntermediate !== false });
        const rows = [['absolute', 'Absolute', COLORS.signalAbsolute],
                      ['intermediate', 'Intermediate', COLORS.signalIntermediate]];
        const pop = document.createElement('div'); pop.id = 'signal-filter-popover';
        pop.style.cssText = 'position:fixed;z-index:3000;background:var(--panel-bg);color:var(--panel-fg);border:1px solid var(--border-strong);border-radius:6px;'
            + 'box-shadow:0 2px 12px var(--shadow);padding:8px 10px;font:12px Arial;min-width:150px;';
        pop.innerHTML = '<div style="font-weight:bold;margin-bottom:6px;">Show signal types</div>';
        for (const [id, label, color] of rows) {
            const row = document.createElement('label');
            row.style.cssText = 'display:flex;align-items:center;gap:6px;padding:2px 0;cursor:pointer;';
            row.innerHTML = '<input type="checkbox" id="sigflt-' + id + '"' + (tv[id] ? ' checked' : '') + '>'
                + '<span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:' + color + ';border:1px solid rgba(0,0,0,.4);"></span>'
                + label;
            pop.appendChild(row);
            row.querySelector('input').addEventListener('change', (e) => setSignalTypeVisible(id, e.target.checked));
        }
        document.body.appendChild(pop);
        const r = anchorBtn.getBoundingClientRect();
        const pw = pop.offsetWidth;
        let left = r.left; if (left + pw > window.innerWidth - 6) left = window.innerWidth - 6 - pw;
        pop.style.top = (r.bottom + 4) + 'px';
        pop.style.left = Math.max(6, left) + 'px';
        setTimeout(() => document.addEventListener('mousedown', signalFilterAway, true), 0);
    }
    function setSignalTypeVisible(type, on) {
        if (!MapApp.signalTypeVisible) MapApp.signalTypeVisible = { absolute: true, intermediate: true };
        MapApp.signalTypeVisible[type] = on;
        rerenderSignals();
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
        let txt = `Trains: ${wt.trains}  ·  Rail vehicles: ${wt.vehicles}`;
        if (MapApp._movingCount != null) txt += `  ·  Moving: ${MapApp._movingCount}`;
        el.textContent = txt;
        el.style.display = 'block';
    }

    // Last world-save simulation time (the save's <date> tag). Live value from
    // serve.py overrides the baked manifest value. "2026-04-11T08:31:52.87Z" ->
    // "2026-04-11 08:31:52".
    function _fmtSimTime(iso) {
        if (!iso) return null;
        const m = String(iso).match(/^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})/);
        return m ? (m[1] + ' ' + m[2]) : String(iso);
    }
    function updateSimTime(iso) {
        const el = document.getElementById('sim-time');
        if (!el) return;
        if (iso !== undefined) MapApp._simTime = iso;   // remember the latest value
        const t = _fmtSimTime(MapApp._simTime
            || (MapApp.manifest && MapApp.manifest.world_sim_time));
        if (!t) { el.style.display = 'none'; return; }
        el.textContent = 'Sim time: ' + t;
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

    function drawTrainOutline(carBodies, layerGroup, highlight) {
        // A single-vehicle train has no consist to connect, so draw no spine
        // (otherwise the end->centre->end line shows as a stub through the one car).
        if (carBodies.length < 2) return;
        const centers = carBodies.map(_midpoint);
        const order = _chainOrder(centers);
        const bodies = order.map(i => carBodies[i]);
        const cen = order.map(i => centers[i]);

        const frontEnd = _outerEnd(bodies[0], cen.length > 1 ? cen[1] : null);
        const rearEnd = _outerEnd(bodies[bodies.length-1], cen.length > 1 ? cen[cen.length-2] : null);

        // Spine: one end -> each car centre -> the other end. A player-crewed train
        // gets a bright, wider spine (a coloured casing) so it stands out; others get
        // the thin dark backbone.
        const line = L.polyline([frontEnd, ...cen, rearEnd], highlight
            ? { color: PLAYER_HL_COLOR, weight: _highlightWeight(rvBodyWeightPx()), opacity: 0.95 }
            : { color: COLORS.trainOutline, weight: TRAIN_STYLE.spine, opacity: 0.9 });
        if (highlight) line._rvHighlight = true;   // rescales with zoom in updateTrainWidths
        line.addTo(layerGroup);
    }

    // Whole-consist totals block for the train hover (issue #51): total length and
    // both tonnages. Trailing tons = the cars the locos pull; Total = everything on
    // the rail incl. locomotives. Same 6-char labels as the RV lines so the colons
    // stay aligned. Returns '' when the train carries no totals (e.g. no RV DB).
    function trainTotalsLines(train) {
        if (!train || train.total_tons == null) return '';
        const n = x => Math.round(x || 0).toLocaleString();
        let out = '<br>──────────<br>';
        if (train.car_count != null) out += `Cars   : ${n(train.car_count)}<br>`;
        if (train.total_length_m) out += `Length : ${n(train.total_length_m * 3.28084)} ft<br>`;
        out += `Trail  : ${n(train.trailing_tons)} t<br>`;   // trailing tons (cars only)
        out += `Total  : ${n(train.total_tons)} t`;          // whole consist incl. locos
        return out;
    }

    function trainVehicleTooltip(train, v) {
        // Fixed-label, monospace layout so the colons align.
        return `<div style="font-family:monospace;white-space:pre;margin:0">`
             + `Train  : ${train.train_id}<br>`
             + `RV num : ${v.unit_number || ''}<br>`
             + `RV tag : ${v.destination_tag || ''}<br>`
             + `RV typ : ${v.car_type || ''}`
             + trainTotalsLines(train)
             + `</div>`;
    }

    function trainVehiclePopup(train, v) {
        let html = `<b>Train ${train.train_id}</b>${train.was_ai ? ' (AI)' : ''}<br>`;
        html += `Unit: ${v.unit_number || 'N/A'}<br>`;
        html += `Type: ${v.unit_type || 'N/A'}<br>`;
        if (v.car_type) html += `Car type: ${v.car_type}<br>`;
        html += `Destination: ${v.destination_tag || 'N/A'}`;
        // Whole-consist totals (issue #51): length, trailing tons (cars only) and
        // total consist weight (incl. locomotives).
        if (train.total_tons != null) {
            const n = x => Math.round(x || 0).toLocaleString();
            html += `<br><span style="color:#555;font-size:12px;">`
                 + `Consist: ${n(train.car_count)} cars`
                 + (train.total_length_m ? `, ${n(train.total_length_m * 3.28084)} ft` : '')
                 + `<br>Trailing ${n(train.trailing_tons)} t · Total ${n(train.total_tons)} t</span>`;
        }
        if (v.rv_filename) html += `<br><span style="color:#888;font-size:11px;">${v.rv_filename}</span>`;
        html += `<br><button type="button" style="margin-top:6px;cursor:pointer;"`
             + ` onclick="MapApp.followTrain(${train.train_id})">Follow this train</button>`;
        return html;
    }

    // ---- Follow a train: auto-center the map on it as it moves each poll ----
    MapApp.followTrainId = null;
    function _followBannerEl() {
        let el = document.getElementById('follow-banner');
        if (!el) {
            el = document.createElement('div');
            el.id = 'follow-banner';
            el.style.cssText = 'position:absolute;top:10px;left:50%;transform:translateX(-50%);'
                + 'z-index:1500;background:rgba(0,0,0,0.78);color:#fff;padding:6px 10px;'
                + 'border-radius:6px;font:13px system-ui,sans-serif;display:none;'
                + 'align-items:center;gap:8px;';
            document.body.appendChild(el);
        }
        return el;
    }
    function _escHtml(s) {
        return String(s).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
    }
    // Lead locomotive = first vehicle in the train's world-save order.
    function _leadVehicle(trainId) {
        for (const [, region] of MapApp.loadedRegions) {
            for (const tr of (region.data && region.data.trains) || []) {
                if (tr.train_id === trainId) return (tr.vehicles && tr.vehicles[0]) || null;
            }
        }
        return null;
    }
    // Banner label: the lead loco's destination tag + unit number (train ID is not
    // interesting to end users), e.g. "Z-LPSD-2040 (#3342)". Falls back gracefully.
    function _followLabel(trainId) {
        const lead = _leadVehicle(trainId);
        if (lead) {
            const tag = (lead.destination_tag || '').trim();
            const unit = (lead.unit_number || '').trim();
            if (tag && unit) return `${_escHtml(tag)} (#${_escHtml(unit)})`;
            if (tag) return _escHtml(tag);
            if (unit) return `#${_escHtml(unit)}`;
        }
        return `${trainId}`;
    }
    function updateFollowBanner() {
        const el = _followBannerEl();
        if (MapApp.followTrainId == null) { el.style.display = 'none'; return; }
        el.innerHTML = `Following Train ${_followLabel(MapApp.followTrainId)} `
            + `<button type="button" style="cursor:pointer;" onclick="MapApp.stopFollow()">Stop</button>`;
        el.style.display = 'flex';
    }
    function _followedLatLngs() {
        const pts = [];
        for (const it of MapApp.trainIndex) {
            if (it.trainId === MapApp.followTrainId && it.layer && it.layer.getLatLngs)
                for (const ll of it.layer.getLatLngs()) pts.push(ll);
        }
        return pts;
    }
    // Re-center on the followed train. `fit` (start of follow) zooms to frame the
    // whole consist once; subsequent calls just pan to keep it centred as it moves.
    function centerOnFollowed(fit) {
        if (MapApp.followTrainId == null) return;
        const pts = _followedLatLngs();
        if (!pts.length) return;   // followed train not currently loaded / placed
        const b = L.latLngBounds(pts);
        if (fit && !MapApp._followFitDone) {
            MapApp.map.fitBounds(b, { padding: [60, 60], maxZoom: 16 });
            MapApp._followFitDone = true;
        } else {
            MapApp.map.panTo(b.getCenter(), { animate: true });
        }
    }
    MapApp.centerOnFollowed = centerOnFollowed;
    MapApp.followTrain = function (trainId) {
        MapApp.followTrainId = trainId;
        MapApp._followFitDone = false;
        if (MapApp.map.closePopup) MapApp.map.closePopup();
        // Following implies the Trains overlay should be on and visible.
        if (!MapApp.overlayStates.trains && typeof toggleOverlay === 'function') toggleOverlay('trains', true);
        updateFollowBanner();
        centerOnFollowed(true);
    };
    MapApp.stopFollow = function () {
        MapApp.followTrainId = null;
        updateFollowBanner();
    };

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
        // Hide any area labels that belonged only to this region's tiles.
        if (typeof updateAreaLabelRegionVisibility === 'function') updateAreaLabelRegionVisibility();
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
        if (typeof updateAreaLabelRegionVisibility === 'function') updateAreaLabelRegionVisibility();
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

        // When toggling industries: colour the industry tracks green (or restore on
        // off), then, if a Local Filter is active, recolour the labels (red matched /
        // grey others). Tracks stay green regardless of the filter.
        if (overlayId === 'industries') {
            updateIndustryTrackColors(enabled);
            if (enabled && MapApp.currentLocalFilter) applyLocalSymbolHighlighting();
        }

        // Grade colour mode (#57): recolour all track by grade (or restore normal/industry
        // colours when turned off) and show/hide the legend. Not a layer group.
        if (overlayId === 'grade') {
            refreshAllTrackColors();
            updateGradeLegend();
        }
    }

    // Colour (or restore) industry tracks. Iterates each region's OWN section layers
    // keyed by that region's id, because section ids collide across regions - the
    // global sectionIndex only keeps the last-loaded region's polyline per id, so a
    // shadowed industry section would be missed (the "green only on mouse-over" bug).
    function updateIndustryTrackColors(showIndustryColor) {
        if (MapApp.overlayStates.grade) return;   // grade colour mode owns the track colours (#57)
        MapApp.loadedRegions.forEach((region, regionId) => {
            if (!region.layers || !region.layers.sections) return;
            region.layers.sections.eachLayer(group => {
                if (!group.eachLayer) return;
                group.eachLayer(pl => {
                    if (!pl._trackLine || !pl.setStyle) return;
                    if (!MapApp.industrySectionIds.has(`${regionId}_${pl._sectionId}`)) return;
                    if (MapApp.selectedSections.has(secKey(regionId, pl._sectionId))) return;  // don't touch selected
                    pl.setStyle({ color: showIndustryColor ? COLORS.industryTrack
                                                           : (pl._origColor || COLORS.track) });
                });
            });
        });
    }

    // ---- Grade heat-map colouring (#57) ----
    // Config comes from manifest.grade ({max_pct, ramp, show_by_default}); precomputed
    // grade_pct rides on each section (and each track polyline as _gradePct).
    function _gradeCfg(){ return (MapApp.manifest && MapApp.manifest.grade) || null; }
    function gradeRamp(){ const g=_gradeCfg(); return (g && g.ramp && g.ramp.length>=2) ? g.ramp
                          : ['#c8c8c8','#ffd400','#ff7a1a','#d7191c']; }
    function gradeMaxPct(){ const g=_gradeCfg(); return (g && g.max_pct>0) ? g.max_pct : 3.0; }
    // Grade shown in tooltips/popups as a magnitude (the stored sign is arbitrary node
    // ordering, so up/down isn't meaningful; the heat map is magnitude too). (#57)
    function gradeText(pct){ return (Math.abs(+pct || 0)).toFixed(2) + '%'; }
    function _hex3(c){ c=String(c||'').replace('#',''); if(c.length===3) c=c.split('').map(x=>x+x).join('');
        return [parseInt(c.slice(0,2),16)||0, parseInt(c.slice(2,4),16)||0, parseInt(c.slice(4,6),16)||0]; }
    function _rgb3(a){ return '#'+a.map(v=>Math.max(0,Math.min(255,Math.round(v))).toString(16).padStart(2,'0')).join(''); }
    // Map |grade%| through the ramp (0 -> first colour, >=max_pct -> last).
    function gradeColor(pct){
        const ramp=gradeRamp(); const t=Math.min(Math.abs(+pct||0)/gradeMaxPct(),1);
        const seg=t*(ramp.length-1); const i=Math.min(Math.floor(seg),ramp.length-2); const f=seg-i;
        const a=_hex3(ramp[i]), b=_hex3(ramp[i+1]);
        return _rgb3([a[0]+(b[0]-a[0])*f, a[1]+(b[1]-a[1])*f, a[2]+(b[2]-a[2])*f]);
    }
    // A track section's resting colour: grade mode wins, then industry green, then its own colour.
    function sectionRestColor(regionId, pl){
        if (MapApp.overlayStates.grade) return gradeColor(pl._gradePct);
        if (MapApp.overlayStates.industries && MapApp.industrySectionIds.has(regionId+'_'+pl._sectionId))
            return COLORS.industryTrack;
        return pl._origColor || COLORS.track;
    }
    // Recolour every (non-selected) track polyline to its current resting colour.
    function refreshAllTrackColors(){
        MapApp.loadedRegions.forEach((region, regionId) => {
            if (!region.layers || !region.layers.sections) return;
            region.layers.sections.eachLayer(group => {
                if (!group.eachLayer) return;
                group.eachLayer(pl => {
                    if (!pl._trackLine || !pl.setStyle) return;
                    if (MapApp.selectedSections.has(secKey(regionId, pl._sectionId))) return;  // keep selection red
                    pl.setStyle({ color: sectionRestColor(regionId, pl) });
                });
            });
        });
    }
    // Floating legend for the grade ramp; present only while grade mode is on.
    function updateGradeLegend(){
        let el = document.getElementById('grade-legend');
        if (!MapApp.overlayStates.grade){ if (el) el.remove(); return; }
        const ramp = gradeRamp(), mx = gradeMaxPct();
        if (!el){
            el = document.createElement('div'); el.id = 'grade-legend';
            el.style.cssText = 'position:absolute;left:10px;z-index:1000;'
                + 'background:var(--panel-bg,rgba(20,22,28,.88));color:var(--panel-fg,#eee);'
                + 'border:1px solid var(--border-strong,#555);border-radius:6px;padding:6px 8px;'
                + 'font:12px system-ui,Arial;box-shadow:0 2px 8px rgba(0,0,0,.4);pointer-events:none;';
            (document.getElementById('map') || document.body).appendChild(el);
        }
        const stops = ramp.map((c,i)=>`${c} ${Math.round(i/(ramp.length-1)*100)}%`).join(',');
        el.innerHTML = '<div style="font-weight:bold;margin-bottom:3px;">Track grade</div>'
            + `<div style="width:150px;height:12px;border:1px solid #888;`
            + `background:linear-gradient(to right,${stops});"></div>`
            + '<div style="display:flex;justify-content:space-between;">'
            + `<span>0%</span><span>${(mx/2).toFixed(1)}%</span><span>${mx}%+</span></div>`;
        // Sit just above the opacity / text-size control box so it isn't obscured by it (#57).
        const oc = document.getElementById('opacity-control');
        const ocBottom = oc ? (parseInt(getComputedStyle(oc).bottom, 10) || 60) : 60;
        el.style.bottom = (oc ? ocBottom + oc.offsetHeight + 8 : 160) + 'px';
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
        // Honour the global "Text Size" slider (#82): the industry tag font is the
        // authored base (11px normal / 10px dimmed) times MapApp.areaFontScale. Unlike
        // area labels these tags are not zoom-collapsed, so the multiplier is the whole
        // scaling. Re-applied by applyLocalSymbolHighlighting() when the slider moves.
        const fScale = MapApp.areaFontScale || 1;
        const fs = (11 * fScale).toFixed(1), fsDim = (10 * fScale).toFixed(1);

        if (!filterActive) {
            // No filter active - normal style
            style = `color:${COLORS.industryTrack};font-size:${fs}px;font-weight:bold;white-space:nowrap;text-shadow:-1px -1px 0 #fff,1px -1px 0 #fff,-1px 1px 0 #fff,1px 1px 0 #fff;`;
        } else if (isHighlighted) {
            // Filter active AND this matches - red text (no background box), same size as normal.
            style = `color:#ff0000;font-size:${fs}px;font-weight:bold;white-space:nowrap;text-shadow:-1px -1px 0 #fff,1px -1px 0 #fff,-1px 1px 0 #fff,1px 1px 0 #fff;`;
        } else {
            // Filter active but doesn't match - dimmed style
            style = `color:#888888;font-size:${fsDim}px;font-weight:normal;white-space:nowrap;text-shadow:none;opacity:0.5;`;
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

    // The Local Filter only recolours the industry LABELS: industries the selected
    // local symbol services turn red, all others dim grey (normal green when no filter).
    // Industry TRACKS are intentionally left in their normal (industry-green) colour -
    // the filter does not recolour the track vectors.
    function applyLocalSymbolHighlighting() {
        const filterSymbol = MapApp.currentLocalFilter;
        const filterActive = filterSymbol !== null;
        for (const [, entry] of MapApp.industryMarkers) {
            const { marker, data } = entry;
            const isMatch = filterSymbol ? (data.local_name === filterSymbol) : true;
            marker.setIcon(createIndustryIcon(data.tag, isMatch, filterActive));
        }
    }

    // Pan/zoom the map to frame every (loaded) industry worked by `symbol` - the
    // Local's footprint. Uses the industry markers' positions; a single industry just
    // centres, several fit their bounds. No-op if none are loaded/visible.
    function zoomToLocal(symbol) {
        if (!symbol || !MapApp.industryMarkers) return;
        const pts = [];
        for (const [, entry] of MapApp.industryMarkers) {
            if (entry.data && entry.data.local_name === symbol && entry.marker.getLatLng) {
                pts.push(entry.marker.getLatLng());
            }
        }
        if (pts.length === 1) {
            MapApp.map.setView(pts[0], Math.max(MapApp.map.getZoom(), 15));
        } else if (pts.length > 1) {
            MapApp.map.fitBounds(L.latLngBounds(pts), { padding: [60, 60], maxZoom: 16 });
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

        const key = secKey(regionId, sectionId);
        if (MapApp.selectedSections.has(key)) {
            // Deselect - restore the section's original colour + the zoom-scaled width.
            MapApp.selectedSections.delete(key);
            const idx = MapApp.sectionIndex.get(key);
            const restoreColor = (idx && idx.originalColor) || COLORS.track;
            featureGroup.eachLayer(layer => {
                if (layer.setStyle) layer.setStyle({
                    color: layer._trackLine ? sectionRestColor(regionId, layer) : restoreColor,
                    weight: trackWeightPx()});
            });

            if (MapApp.selectedSections.size === 0) {
                MapApp.currentSelectionRegion = null;
            }
        } else {
            // Select - apply style to all layers in the feature group
            MapApp.selectedSections.set(key, {polyline: featureGroup, metadata, region_id: regionId});
            MapApp.currentSelectionRegion = regionId;
            featureGroup.eachLayer(layer => {
                if (layer.setStyle) layer.setStyle({color: COLORS.trackSelected, weight: 5});
            });
        }

        updateSelectionInfo();
    }

    function clearSelection() {
        for (const [key, data] of MapApp.selectedSections) {
            const idx = MapApp.sectionIndex.get(key);
            const restoreColor = (idx && idx.originalColor) || COLORS.track;
            data.polyline.eachLayer(layer => {
                if (layer.setStyle) layer.setStyle({
                    color: layer._trackLine ? sectionRestColor(data.region_id, layer) : restoreColor,
                    weight: trackWeightPx()});
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
        content += `Grade: ${gradeText(section.grade_pct)}<br>`;
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
            // Match on the real (per-region) section id, not the region-qualified map key.
            for (const [, data] of MapApp.sectionIndex) {
                const sid = data.metadata.id;
                if (sid.toString().includes(query) || sid === queryNum) {
                    results.push({
                        type: 'section',
                        id: sid,
                        label: `Section ${sid}`,
                        region: data.region_id,
                        data: data
                    });
                }
            }
        } else if (searchType === 'signal') {
            const queryNum = parseInt(query);
            // Match on the real (per-region) signal id, not the region-qualified map key.
            for (const [, data] of MapApp.signalIndex) {
                const sig = data.metadata.id;
                if (sig.toString().includes(query) || sig === queryNum) {
                    results.push({
                        type: 'signal',
                        id: sig,
                        label: `Signal ${sig}`,
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
            // Match on the field chosen by the radios (default All): Unit #, Tag,
            // Train ID, or all three (substring, case-insensitive).
            const field = (document.querySelector('input[name="train-field"]:checked') || {}).value || 'all';
            for (let i = 0; i < MapApp.trainIndex.length; i++) {
                const it = MapApp.trainIndex[i];
                const v = it.vehicle;
                const hay = (
                    field === 'unit'    ? (v.unit_number || '') :
                    field === 'tag'     ? (v.destination_tag || '') :
                    field === 'trainId' ? String(it.trainId) :
                    [String(it.trainId), v.unit_number || '', v.destination_tag || ''].join(' ')
                ).toLowerCase();
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
        } else if (searchType === 'area') {
            // Area labels live only in the align viewer (MapApp.areaMarkers); match
            // on the label text or its id (case-insensitive substring). The id is
            // carried so goToResult can look the marker back up.
            const markers = MapApp.areaMarkers || [];
            for (const rec of markers) {
                const a = rec.area || {};
                const label = a.label || '';
                if (label.toLowerCase().includes(query)
                    || String(a.id || '').toLowerCase().includes(query)) {
                    results.push({
                        type: 'area',
                        id: a.id,
                        label: label || String(a.id || '(label)'),
                        region: (a.type || 'label'),
                        data: rec
                    });
                }
            }
        } else if (searchType === 'tile') {
            // Free-text tile coord: 2 numbers = a tile (jump to its centre), 4 =
            // tile + Run8 local x,z. Accepts commas and/or spaces, and negatives.
            const nums = (query.match(/-?\d+(?:\.\d+)?/g) || []).map(Number);
            if (nums.length >= 2) {
                const tx = Math.trunc(nums[0]), tz = Math.trunc(nums[1]);
                const hasLocal = nums.length >= 4;
                const idStr = hasLocal ? `${tx},${tz},${nums[2]},${nums[3]}` : `${tx},${tz}`;
                // Best-effort: name the loaded region whose track passes through this tile.
                let hint = 'jump';
                for (const [rid, region] of (MapApp.loadedRegions || new Map())) {
                    const rd = region && region.data;
                    if (rd && rd.tiles && rd.tiles.some(t => t.x === tx && t.z === tz)) {
                        hint = rd.display_name || rid; break;
                    }
                }
                results.push({
                    type: 'tile', id: idStr, region: hint, data: null,
                    label: hasLocal
                        ? `Tile ${tx}, ${tz}  (local ${nums[2]}, ${nums[3]})`
                        : `Tile ${tx}, ${tz}`
                });
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
            const data = MapApp.sectionIndex.get(secKey(regionId, id));
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
            const data = MapApp.signalIndex.get(secKey(regionId, id));
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
        } else if (type === 'area') {
            // Area labels: find the marker by id, make its overlay/category visible,
            // pan to it and give it a brief flash so it's easy to spot.
            const rec = (MapApp.areaMarkers || []).find(r => r.area && r.area.id === id);
            if (rec && rec.marker) {
                if (typeof ensureAreaOverlayVisible === 'function') ensureAreaOverlayVisible();
                if (typeof ensureAreaTypeVisible === 'function') ensureAreaTypeVisible(rec.type);
                if (typeof updateAreaLabelSizes === 'function') updateAreaLabelSizes();
                const ll = rec.marker.getLatLng();
                // Zoom in enough to clear this type's zoom gate (#58), else the found
                // label would stay hidden. Reveal + flash after the move settles, since a
                // gated group is only (re)added on zoomend.
                const gz = (typeof zoomToRevealAreaType === 'function') ? zoomToRevealAreaType(rec.type, ll.lat) : 0;
                const need = Math.max(MapApp.map.getZoom(), 14, gz);
                const willZoom = need > MapApp.map.getZoom();
                MapApp.map.setView(ll, need);
                if (willZoom) MapApp.map.once('moveend', () => { updateAreaLabelSizes(); flashAreaMarker(rec.marker); });
                else flashAreaMarker(rec.marker);
            }
        } else if (type === 'tile') {
            // Jump to a tile coord (id = "tx,tz" or "tx,tz,localX,localZ"). Same
            // tile->world->latlng path area labels use (inlined - areaToWorld lives
            // in the align layer); worldToLatLon applies the current manual
            // alignment, so it lands where that tile sits on the map right now.
            const tp = MapApp.manifest && MapApp.manifest.tile_params;
            if (tp && typeof MapApp.worldToLatLon === 'function') {
                const p = String(id).split(',').map(Number);
                const tx = Math.trunc(p[0]), tz = Math.trunc(p[1]);
                const lx = (p.length >= 4 && isFinite(p[2])) ? p[2] : tp.tile_width / 2;
                const lz = (p.length >= 4 && isFinite(p[3])) ? p[3] : tp.tile_height / 2;
                const worldX = (tx - tp.home_tile[0]) * tp.tile_width + lx;
                const worldY = (tz - tp.home_tile[1]) * tp.tile_height - lz;
                MapApp.map.setView(MapApp.worldToLatLon(worldX, worldY),
                                   Math.max(MapApp.map.getZoom(), 15));
                // Turn Tile Boundaries on if they're off, so the jumped-to tile is visible.
                if (!MapApp.overlayStates.tileBoundaries) {
                    const cb = document.getElementById('overlay-tileBoundaries');
                    if (cb) cb.checked = true;
                    toggleOverlay('tileBoundaries', true);
                }
                flashTile(tx, tz);
            }
        }
    }

    // Briefly pulse an area-label marker so a search hit is easy to find. Only the
    // CSS `filter` is animated (a glow) - never transform, which Leaflet owns for
    // positioning and the label uses for rotation.
    function flashAreaMarker(marker) {
        const el = marker && marker.getElement && marker.getElement();
        if (!el) return;
        const prev = el.style.transition;
        el.style.transition = 'filter 0.15s';
        let on = 0;
        const t = setInterval(() => {
            on ^= 1;
            el.style.filter = on ? 'drop-shadow(0 0 7px #ffcc00) brightness(1.35)' : 'none';
        }, 180);
        setTimeout(() => {
            clearInterval(t);
            el.style.filter = 'none';
            el.style.transition = prev;
        }, 1300);
    }

    // Briefly pulse an outline over a whole tile so a Tile-coord search hit is easy to
    // spot. Uses the tile's own drawn bounds when its region is loaded (an exact match
    // to the boundary rectangle); otherwise derives the box from world coords so an
    // off-track / unloaded tile still highlights. Auto-removes after ~1.6 s.
    function flashTile(tx, tz) {
        let latlngs = null;
        for (const [, region] of (MapApp.loadedRegions || new Map())) {
            const rd = region && region.data;
            if (!rd || !rd.tiles) continue;
            const t = rd.tiles.find(tt => tt.x === tx && tt.z === tz);
            if (t) {   // post-transformData these are aligned lat/lon (SW + NE corners)
                latlngs = [[t.lat_south, t.lon_west], [t.lat_north, t.lon_west],
                           [t.lat_north, t.lon_east], [t.lat_south, t.lon_east]];
                break;
            }
        }
        if (!latlngs) {
            const tp = MapApp.manifest && MapApp.manifest.tile_params;
            if (!tp || typeof MapApp.worldToLatLon !== 'function') return;
            const bx = (tx - tp.home_tile[0]) * tp.tile_width;
            const by = (tz - tp.home_tile[1]) * tp.tile_height;
            const corners = [[bx, by], [bx + tp.tile_width, by],
                             [bx + tp.tile_width, by - tp.tile_height], [bx, by - tp.tile_height]];
            latlngs = corners.map(c => MapApp.worldToLatLon(c[0], c[1]));
        }
        const poly = L.polygon(latlngs, {
            color: '#ffcc00', weight: 3, opacity: 1,
            fill: true, fillColor: '#ffcc00', fillOpacity: 0.25, interactive: false
        }).addTo(MapApp.map);
        let on = 1;
        const timer = setInterval(() => {
            on ^= 1;
            poly.setStyle({ opacity: on ? 1 : 0.3, fillOpacity: on ? 0.25 : 0.05 });
        }, 220);
        setTimeout(() => {
            clearInterval(timer);
            if (MapApp.map.hasLayer(poly)) MapApp.map.removeLayer(poly);
        }, 1600);
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
        clearSelection: clearSelection,
        // Exposed for inline onclick handlers (train popup / follow banner).
        followTrain: MapApp.followTrain,
        stopFollow: MapApp.stopFollow
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


ALIGN_JS = r'''
    // ================= Manual alignment (contiguous tile-based track over real map) =================
    MapApp.align = null; MapApp._raw = {}; MapApp.trackOpacity = 0.8; MapApp.areaFontScale = 1.0;
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
        const row = document.createElement('label');
        row.innerHTML = '<span class="oc-lbl">Track Opacity:</span><input type="range" id="track-opacity-slider" min="0" max="100" value="'
            + Math.round(MapApp.trackOpacity * 100) + '">';
        ctl.appendChild(row);
        document.getElementById('track-opacity-slider').addEventListener('input', (e)=>{
            MapApp.trackOpacity = e.target.value/100; applyTrackOpacity();
        });
        applyTrackOpacity();
    }
    // Global "Text Size" slider (sits under Track Opacity): a relative multiplier on
    // every on-map text label, applied at render time. It scales area labels
    // (updateAreaLabelSizes), industry tags (applyLocalSymbolHighlighting re-renders
    // their divIcons) and rail-vehicle destination tags (updateTrainLabelSizes) - #82.
    // It does NOT change stored/authored sizes, and deliberately does NOT touch tooltip
    // pop-ups; it only scales how big the map text looks in the live view.
    function updateAllTextSizes(){
        updateAreaLabelSizes();
        if (typeof applyLocalSymbolHighlighting === 'function') applyLocalSymbolHighlighting();
        if (typeof updateTrainLabelSizes === 'function') updateTrainLabelSizes();
    }
    function addAreaLabelFontSlider(){
        const ctl = document.getElementById('opacity-control');
        if (!ctl) return;
        const row = document.createElement('label');
        row.innerHTML = '<span class="oc-lbl">Text Size:</span><input type="range" id="area-font-slider" min="50" max="300" step="5" value="'
            + Math.round(MapApp.areaFontScale * 100) + '">';
        ctl.appendChild(row);
        document.getElementById('area-font-slider').addEventListener('input', (e)=>{
            MapApp.areaFontScale = e.target.value/100;
            updateAllTextSizes();
        });
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
        addAreaLabelFontSlider();
        MapApp.overlayStates.areaLabels = false;
        addAreaLabelToggle();
        addTrainOptionsButton();
        updateSimTime();   // show baked sim time (the live poll overrides it later)
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
        let style = `display:inline-block;color:${color};font-size:${fontSize}px;font-weight:bold;white-space:nowrap;text-align:center;`;
        if (area.box) style += `background:rgba(0,0,0,0.6);padding:2px 6px;border-radius:3px;text-shadow:0 1px 2px rgba(0,0,0,0.8);`;
        else style += `text-shadow:-1px -1px 0 #000,1px -1px 0 #000,-1px 1px 0 #000,1px 1px 0 #000;`;
        const rot = area.rotation ? ` rotate(${area.rotation}deg)` : '';
        style += `transform:translate(-50%,-50%)${rot};`;
        // Escape the label, then honour embedded newlines as line breaks (multi-line labels).
        const labelHtml = escapeHtml(area.label).replace(/\n/g, '<br>');
        return L.divIcon({ className:'area-label-marker',
            html:`<div style="${style}">${labelHtml}</div>`, iconSize:null, iconAnchor:[0,0] });
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
    // ---- Region gating for area labels ----
    // A label is shown only when its tile falls inside a currently-visible region.
    // Each region's JSON carries the set of tiles its track passes through, so once
    // a region is loaded+visible we know which labels belong to it (by tile). Labels
    // whose tile is in no visible region (region off, or an off-track label) hide.
    function _visibleRegionTiles(){
        const set = new Set();
        MapApp.loadedRegions.forEach(region => {
            if (!region.visible || !region.data || !region.data.tiles) return;
            for (const t of region.data.tiles) set.add(t.x + ',' + t.z);
        });
        return set;
    }
    // How many tiles away a visible region's track may be for a label whose OWN tile
    // carries no track to still count as "in" that region (best-guess for labels
    // placed just off the rails, e.g. a milepost / siding name on a track-less tile).
    // Kept small so it can't reach an unrelated neighbouring region; 0 = exact tile only.
    const AREA_LABEL_REGION_FALLBACK_TILES = 2;
    function _areaInVisibleRegion(area, tileSet){
        const tx = area.tile_x, tz = area.tile_z;
        if (tileSet.has(tx + ',' + tz)) return true;              // exact tile has track
        const R = AREA_LABEL_REGION_FALLBACK_TILES;
        for (let dx = -R; dx <= R; dx++)
            for (let dz = -R; dz <= R; dz++){
                if (!dx && !dz) continue;
                if (tileSet.has((tx + dx) + ',' + (tz + dz))) return true;  // near a visible region
            }
        return false;
    }
    // Add/remove each label marker from its (type) layer to match the current set of
    // visible regions. Called whenever a region is shown/hidden (and after building).
    function updateAreaLabelRegionVisibility(){
        if (!MapApp.areaMarkers) return;
        const tileSet = _visibleRegionTiles();
        for (const rec of MapApp.areaMarkers){
            const lg = (MapApp.areaTypeLayers && MapApp.areaTypeLayers[rec.type]) || MapApp.areaLabelsLayer;
            if (!lg) continue;
            const want = _areaInVisibleRegion(rec.area, tileSet);
            const has = lg.hasLayer(rec.marker);
            if (want && !has) lg.addLayer(rec.marker);
            else if (!want && has) lg.removeLayer(rec.marker);
        }
        if (MapApp.overlayStates.areaLabels) updateAreaLabelSizes();
    }
    function buildAreaLabels(){
        MapApp.areaLabelsLayer = L.layerGroup();
        MapApp.areaMarkers = [];
        MapApp.areaTypeLayers = {};
        initAreaTypeVisible();
        for (const id of areaGroupIds()){
            const lg = L.layerGroup();
            MapApp.areaTypeLayers[id] = lg;
            applyAreaTypeLayer(id);   // add if filter ON and (zoom gate #58) OK
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
        // Only place the marker if its tile is inside a currently-visible region;
        // updateAreaLabelRegionVisibility() keeps this in sync as regions toggle.
        const lg = (MapApp.areaTypeLayers && MapApp.areaTypeLayers[rec.type]) || MapApp.areaLabelsLayer;
        if (_areaInVisibleRegion(area, _visibleRegionTiles())) lg.addLayer(marker);
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
    // Per-type zoom gate (#58): a [label_types] entry may set max_scale_m so a dense
    // category (e.g. yard-track labels) shows only when zoomed in to that scale bar or
    // tighter. 0/absent = always. Undefined-type labels (AREA_UNDEFINED) have no entry
    // and are never gated. Compared against the same _scaleBarMeters() the train tags use.
    function areaTypeMaxScaleM(type){
        const t = labelTypes().find(x => x.id === String(type||'').toLowerCase());
        const m = t && t.max_scale_m;
        return (typeof m === 'number' && m > 0) ? m : 0;
    }
    function zoomOkForAreaType(type){
        const maxM = areaTypeMaxScaleM(type);
        return maxM <= 0 || _scaleBarMeters() <= maxM;
    }
    // Smallest map zoom at which a gated type's gate passes at latitude `lat` (0 = not
    // gated). Leaflet's scale bar is <= 100*mpp, so 100*mpp <= maxM guarantees the gate;
    // used so a search hit on a gated label zooms in enough to actually show it (#58).
    function zoomToRevealAreaType(type, lat){
        const maxM = areaTypeMaxScaleM(type);
        if (maxM <= 0) return 0;
        return Math.ceil(Math.log2(156543.03392 * Math.cos(lat*Math.PI/180) * 100 / maxM));
    }
    // Add/remove a type's layer group from the master to match (filter ON) AND (zoom OK).
    function applyAreaTypeLayer(type){
        const lg = MapApp.areaTypeLayers && MapApp.areaTypeLayers[type];
        if (!lg || !MapApp.areaLabelsLayer) return;
        const want = !!(MapApp.areaTypeVisible && MapApp.areaTypeVisible[type]) && zoomOkForAreaType(type);
        const has = MapApp.areaLabelsLayer.hasLayer(lg);
        if (want && !has) MapApp.areaLabelsLayer.addLayer(lg);
        else if (!want && has) MapApp.areaLabelsLayer.removeLayer(lg);
    }
    // Re-evaluate every type's zoom gate (called on zoomend via updateAreaLabelSizes).
    function updateAreaTypeZoomVisibility(){
        if (!MapApp.areaTypeLayers) return;
        for (const id of areaGroupIds()) applyAreaTypeLayer(id);
    }
    function setAreaTypeVisible(type, on){
        if (!MapApp.areaTypeVisible) return;
        MapApp.areaTypeVisible[type] = on;   // filter checkbox state
        applyAreaTypeLayer(type);            // combines with the zoom gate
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
        // Recover from background-timer throttling / machine sleep: when the tab
        // becomes visible again, browsers may have stalled the 3s interval, leaving
        // the map frozen on stale positions. Force an immediate refresh (and revive
        // the interval if it was cleared), so returning to the tab self-heals with
        // no reload needed. Reset the version guard so the re-poll always re-renders.
        if (!MapApp._trainsVisHooked){
            MapApp._trainsVisHooked = true;
            document.addEventListener('visibilitychange', () => {
                if (document.visibilityState !== 'visible') return;
                MapApp.trainsVersion = null;
                if (!MapApp._trainsPoll) MapApp._trainsPoll = setInterval(pollTrainsOnce, 3000);
                pollTrainsOnce();
            });
        }
    }
    function applyLiveTrains(j){
        if (!j || !j.trains) return;
        // Whole-save totals are region-independent; update the status line even if
        // the render below early-returns (no regions loaded yet / unchanged version).
        if ('moving_count' in j) MapApp._movingCount = j.moving_count;
        if (j.totals) { MapApp._liveTotals = j.totals; updateTrainCount(); }
        if ('sim_time' in j) updateSimTime(j.sim_time);   // live save clock
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
        // Keep the followed train centred as its new positions land.
        if (MapApp.centerOnFollowed) MapApp.centerOnFollowed(false);
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
        updateAreaTypeZoomVisibility();   // add/remove zoom-gated type groups for the current zoom (#58)
        const mpp = 156543.03392 * Math.cos(MapApp.map.getCenter().lat*Math.PI/180) / Math.pow(2, MapApp.map.getZoom());
        const scaleM = 100 * mpp;
        const lo = Math.log(AREA_LABEL_SCALE_MAX_M), hi = Math.log(AREA_LABEL_SCALE_MIN_M);
        let t = (Math.log(scaleM) - lo) / (hi - lo); t = Math.max(0, Math.min(1, t));
        const scale = MapApp.areaFontScale || 1;   // global "Label Size" multiplier
        for (const rec of MapApp.areaMarkers){
            const el = rec.marker.getElement(); if (!el || !el.firstChild) continue;
            const effBase = rec.baseFont * scale;   // scaled authored size; still collapses toward the floor when zoomed out
            el.firstChild.style.fontSize = (effBase + t*(AREA_LABEL_MIN_PX - effBase)).toFixed(1) + 'px';
        }
    }
    function addAreaLabelToggle(){
        const list = document.getElementById('overlay-list');
        if (!list) return;
        initAreaTypeVisible();
        // Master "Area Labels" toggle + a "Filter" button that opens the per-category
        // checkboxes in a popover (keeps the overlay panel tidy).
        // The .overlay-item class already lays this out like the other rows (flex +
        // an 8px label margin); the Filter button uses margin-left:auto to sit at the
        // right. Adding a `gap` here would misalign this row's label - so don't.
        const item = document.createElement('div'); item.className = 'overlay-item';
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
        pop.style.cssText = 'position:fixed;z-index:3000;background:var(--panel-bg);color:var(--panel-fg);border:1px solid var(--border-strong);border-radius:6px;'
            + 'box-shadow:0 2px 12px var(--shadow);padding:8px 10px;font:12px Arial;min-width:150px;';
        pop.innerHTML = '<div style="font-weight:bold;margin-bottom:6px;">Show label types</div>';
        for (const [id,label,color] of rows){
            const row = document.createElement('label');
            row.style.cssText = 'display:flex;align-items:center;gap:6px;padding:2px 0;cursor:pointer;';
            // Hint that a zoom-gated type (#58) only appears when zoomed in far enough.
            const gated = areaTypeMaxScaleM(id) > 0
                ? ' <span style="color:var(--muted,#888);font-size:10px;">(zoom-in)</span>' : '';
            row.innerHTML = '<input type="checkbox" id="overlay-areaType-'+id+'"'+(MapApp.areaTypeVisible[id]?' checked':'')+'>'
                + '<span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:'+(color||'#ffffff')+';border:1px solid rgba(0,0,0,.4);"></span>'
                + label + gated;
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

    // ---- Trains "Options" button + popover (display options for the Trains overlay) ----
    // Inject an "Options" button beside the existing Trains overlay checkbox, mirroring
    // the Area Labels "Filter" button.
    function addTrainOptionsButton(){
        const cb = document.getElementById('overlay-trains');
        if (!cb) return;
        // The .overlay-item class is already flex with a label margin matching the
        // other rows; just append the button (margin-left:auto pushes it right). Do
        // NOT add a `gap` here - it would shift this row's label out of alignment.
        const item = cb.closest('.overlay-item') || cb.parentElement;
        const btn = document.createElement('button');
        btn.id = 'train-options-btn'; btn.type = 'button';
        btn.title = 'Train display options';
        btn.textContent = 'Filter';
        btn.style.cssText = 'margin-left:auto;font-size:11px;padding:1px 7px;cursor:pointer;';
        item.appendChild(btn);
        btn.addEventListener('click', (e)=>{ e.stopPropagation(); toggleTrainOptionsPopover(e.currentTarget); });
    }
    function closeTrainOptionsPopover(){
        const pop = document.getElementById('train-options-popover');
        if (pop) pop.remove();
        document.removeEventListener('mousedown', trainOptionsAway, true);
    }
    function trainOptionsAway(e){
        const pop = document.getElementById('train-options-popover');
        if (pop && !pop.contains(e.target) && e.target.id !== 'train-options-btn') closeTrainOptionsPopover();
    }
    function toggleTrainOptionsPopover(anchorBtn){
        if (document.getElementById('train-options-popover')){ closeTrainOptionsPopover(); return; }
        const o = MapApp.trainOptions;
        const rows = [
            ['coloredCars', 'Show colored cars', 'Colour non-loco cars by type. Off: all cars use the box-car colour.'],
            ['showCuts', 'Show cuts of cars', 'Also plot loose cuts of cars (rail vehicles not led by a locomotive).'],
            ['showOnlyMoving', 'Show only moving', 'Only plot trains that moved since the last world save (live only).'],
            ['highlightPlayers', 'Highlight player trains', 'Highlight trains that are moving and not AI-crewed (likely player-driven) with a bright spine (live only).']
        ];
        const pop = document.createElement('div'); pop.id = 'train-options-popover';
        pop.style.cssText = 'position:fixed;z-index:3000;background:var(--panel-bg);color:var(--panel-fg);border:1px solid var(--border-strong);border-radius:6px;'
            + 'box-shadow:0 2px 12px var(--shadow);padding:8px 10px;font:12px Arial;min-width:170px;';
        pop.innerHTML = '<div style="font-weight:bold;margin-bottom:6px;">Train display options</div>';
        for (const [key,label,tip] of rows){
            const row = document.createElement('label');
            row.title = tip;
            row.style.cssText = 'display:flex;align-items:center;gap:6px;padding:2px 0;cursor:pointer;';
            row.innerHTML = '<input type="checkbox"'+(o[key]?' checked':'')+'>' + label;
            pop.appendChild(row);
            row.querySelector('input').addEventListener('change', (e)=>{ o[key] = e.target.checked; applyTrainOptions(); });
        }
        document.body.appendChild(pop);
        const r = anchorBtn.getBoundingClientRect();
        const pw = pop.offsetWidth;
        let left = r.left; if (left + pw > window.innerWidth - 6) left = window.innerWidth - 6 - pw;
        pop.style.top = (r.bottom + 4) + 'px';
        pop.style.left = Math.max(6, left) + 'px';
        setTimeout(()=> document.addEventListener('mousedown', trainOptionsAway, true), 0);
    }
    // Re-render every loaded region's trains with the current options (same as the
    // LOD re-render). Cheap: reuses each region's already-loaded data.
    function applyTrainOptions(){
        MapApp.loadedRegions.forEach((region, regionId) => {
            if (!region.layers || !region.layers.trains) return;
            region.layers.trains.clearLayers();
            region.layers.trainLabels.clearLayers();
            renderTrains(regionId, region.data, region.layers);
            if (MapApp.overlayStates.trains) region.layers.trains.addTo(MapApp.map);
        });
        updateTrainLabelVisibility();
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
            <label style="display:block;margin:6px 0 2px;">Label text <span style="color:var(--text-muted);font-weight:normal;">(Enter = new line, Ctrl+Enter = save)</span>:</label>
            <textarea id="al-text" rows="2" placeholder="e.g. Barstow Yard" style="width:100%;box-sizing:border-box;padding:4px;resize:vertical;font:inherit;">${escapeHtml(area.label)}</textarea>
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
            <div style="margin-top:6px;color:var(--text-muted);">tile ${area.tile_x},${area.tile_z} &nbsp; local ${(+area.local_x).toFixed(1)},${(+area.local_z).toFixed(1)}</div>
            ${isNew ? '' : '<div style="margin-top:4px;color:var(--text-muted);font-style:italic;">Tip: drag to move &middot; hold the mouse button on it and scroll to rotate.</div>'}
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
            // Enter inserts a newline (multi-line labels); Ctrl/Cmd+Enter saves.
            textEl.addEventListener('keydown', (ev)=>{ if (ev.key === 'Enter' && (ev.ctrlKey || ev.metaKey)){ ev.preventDefault(); save(); } });
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
            <label style="display:block;margin:6px 0 2px;">Label text <span style="color:var(--text-muted);">(Enter = new line, Ctrl+Enter = generate)</span>:</label>
            <textarea id="al-text" rows="2" placeholder="e.g. Barstow Yard" style="width:100%;box-sizing:border-box;padding:4px;resize:vertical;font:inherit;"></textarea>
            <label style="display:block;margin:6px 0 2px;">Type <select id="al-type" style="padding:2px;">${areaTypeSelectOptions(area.type)}</select></label>
            <div style="margin-top:6px;color:var(--text-muted);">tile ${tileX},${tileZ} &nbsp; local ${localX.toFixed(1)},${localZ.toFixed(1)} &nbsp; rot ${rotation}&deg;</div>
            <button id="al-gen" style="margin-top:8px;padding:4px 8px;cursor:pointer;">Generate INI</button>
            <pre id="al-out" style="display:none;white-space:pre-wrap;background:var(--hover-bg);padding:6px;margin-top:6px;border-radius:4px;font-size:11px;"></pre>
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
                const iniLabel=(label||'New Label').replace(/\r?\n/g,'\\n');   // multi-line -> literal \n
                let ini=`[area.${slug}]\nlabel = ${iniLabel}\ntile = ${tileX},${tileZ}\nlocal = ${localX.toFixed(1)},${localZ.toFixed(1)}`;
                if (rotation) ini+=`\nrotation = ${rotation}`;
                const atype=(document.getElementById('al-type')||{}).value;
                if (atype && atype!=='other') ini+=`\ntype = ${atype}`;
                outEl.textContent=ini; outEl.style.display='block'; copyBtn.style.display='inline-block';
            };
            genBtn.addEventListener('click', generate);
            // Enter inserts a newline (multi-line labels); Ctrl/Cmd+Enter generates.
            textEl.addEventListener('keydown', (ev)=>{ if (ev.key==='Enter' && (ev.ctrlKey || ev.metaKey)){ ev.preventDefault(); generate(); } });
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
          'background:var(--panel-bg);color:var(--panel-fg);border:1px solid var(--border-strong);border-radius:6px;box-shadow:0 1px 6px var(--shadow);font:13px Arial';
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
        // Sit Help directly under the Align-mode button (same left, second row) so it
        // doesn't float far to the right when the Add Label button is absent
        // (production build). bs sets top:10px; the trailing top:46px overrides it.
        help.style.cssText=bs+';left:52px;top:46px';
        document.body.appendChild(help);
        let helpEl=null;
        function kbd(s){ return '<kbd style="background:var(--kbd-bg);border:1px solid var(--border);border-radius:3px;padding:0 5px;font:12px monospace">'+s+'</kbd>'; }
        function hrow(t,d){ return '<dt style="font-weight:600;margin-top:10px">'+t+'</dt>'
            +'<dd style="margin:2px 0 0 0;color:var(--text-muted)">'+d+'</dd>'; }
        function toggleHelp(show){
            if(!helpEl){
                helpEl=document.createElement('div');
                helpEl.style.cssText='position:absolute;inset:0;z-index:3000;display:none;background:rgba(0,0,0,.35)';
                const card=document.createElement('div');
                card.style.cssText='position:absolute;top:50px;left:52px;max-width:430px;max-height:80vh;'
                    +'overflow:auto;background:var(--panel-bg);color:var(--panel-fg);border-radius:8px;box-shadow:0 4px 20px var(--shadow);'
                    +'padding:16px 20px;font:13px/1.5 Arial';
                card.innerHTML=
                    '<div style="display:flex;justify-content:space-between;align-items:center;'
                    +'border-bottom:1px solid var(--border);padding-bottom:8px;margin-bottom:6px">'
                    +'<h3 style="margin:0;font:600 15px Arial">Map controls &amp; tips</h3>'
                    +'<button id="help-close" title="Close" style="border:none;background:var(--kbd-bg);color:var(--panel-fg);border-radius:4px;'
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
        function updA(){ btn.textContent='Align mode: '+(aligning?'ON':'OFF'); btn.style.background=aligning?'#1560d0':'var(--panel-bg)'; btn.style.color=aligning?'#fff':'var(--panel-fg)'; }
        function updL(){ if(!lbl) return; lbl.textContent='Add Label: '+(MapApp.labelMode?'ON':'OFF'); lbl.style.background=MapApp.labelMode?'#1a9a4a':'var(--panel-bg)'; lbl.style.color=MapApp.labelMode?'#fff':'var(--panel-fg)'; }
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
<script>window.TRAIN_STYLE = {{car: {config.train_car_width}, spine: {config.train_spine_width}, carM: {config.train_car_width_m}, labelScaleM: {config.train_label_scale_m}, labelSize: {config.train_label_size}, lodScaleM: {config.train_lod_scale_m}, lodMinCars: {config.train_lod_min_cars}, lodColor: '{config.train_lod_color}'}};
window.TRACK_STYLE = {{width: {config.track_width}, minWidth: {config.track_min_width}, fullZoom: {config.track_full_zoom}}};
window.SIGNAL_STYLE = {{sizeM: {config.signal_size_m}, showIntermediate: {str(config.signal_show_intermediate).lower()}}};</script>
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

    # generate_output writes the region JSON files and the align index.html.
    generate_output(config)
