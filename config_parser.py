#!/usr/bin/env python3
"""
Multi-region configuration parser for Run8 Track Mapper.

Parses INI files with the following structure:

[visualization]
name = Southern California
tile_corrections = tile_corrections_socal.csv
industry_db = path/to/Regions/SouthernCA/Config.ind

[region.mojave]
display_name = Mojave Subdivision
route_prefix = 1
directory = path/to/V3Routes/BNSF_MojaveSub
enabled_by_default = true

[region.barstow]
...

[output]
output_dir = ./output/socal/
"""

import configparser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# Standard filenames within each region directory
TRACK_DB_FILENAME = "TrackDatabase.r8"
SIGNAL_DB_FILENAME = "SignalHeadDatabase.r8"
AI_LOCATIONS_FILENAME = "AiSpecialLocations.r8"
MILEPOST_DB_FILENAME = "MilepostDatabase.r8"
INDUSTRY_DB_FILENAME = "Config.ind"
TERRAIN_DIR_NAME = "TerrainTiles"


@dataclass
class TileBasedConfig:
    """Configuration for tile-based plotting (non-geographic coordinates)"""
    home_tile: Tuple[int, int] = (0, 0)  # Reference tile for origin
    tile_width: float = 842.3            # Tile width in meters
    tile_height: float = 1023.2          # Tile height in meters


# Area/place label categories are defined entirely by the config's [label_types]
# section (id = Display Name, #color) — no categories are baked into the code. A
# label with no type, or a type not present in [label_types], is treated as
# "undefined": rendered in UNDEFINED_TYPE_COLOR (white) and grouped as AREA_TYPE_DEFAULT.
AREA_TYPE_DEFAULT = "other"
UNDEFINED_TYPE_COLOR = "#ffffff"

# Color presets are defined entirely by the config's [color_presets] section
# (no railroad-specific colors are baked into the code). A label's `color` may be a
# preset NAME (e.g. "bnsf") or a raw hex value; the name is stored as-is and resolved
# to hex at render time (viewer). Keys are compared case-insensitively.
DEFAULT_COLOR_PRESETS = {}


def resolve_color(value, presets=None):
    """Resolve a preset name to its hex; pass raw hex/other values through trimmed."""
    if not value:
        return value
    table = presets if presets is not None else DEFAULT_COLOR_PRESETS
    return table.get(value.strip().lower(), value.strip())


@dataclass
class ColorConfig:
    """Color configuration for visualization elements"""
    track: str = "#0066cc"              # Regular track sections
    track_selected: str = "#ff0000"     # Selected track sections
    track_hover: str = "#ffff00"        # Hover highlight color (yellow)
    switch: str = "#800080"             # Switch/turnout sections (purple)
    industry_track: str = "#00aa00"     # Industry track sections (green)
    signal_absolute: str = "#FF6B35"    # Absolute signals (orange)
    signal_intermediate: str = "#FFD700"  # Intermediate signals (gold)
    signal_border_single: str = "#000000"  # Single head signal border (black)
    signal_border_stacked: str = "#87CEEB"  # Multiple head signal border (light blue)
    background: str = "#333333"         # Background color (tile-based mode)
    area_label: str = "#ffd11a"         # Legacy generic label color (kept for compatibility)
    train: str = "#8B0000"              # Rail-vehicle body (cars) from a world save (dark red)
    train_loco: str = "#B22222"         # Locomotive body (firebrick, to head-mark a consist)
    train_outline: str = "#000000"      # Thin spine joining a train's cars


@dataclass
class LabelType:
    """A user-defined area-label category, from the config's [label_types] section."""
    id: str            # slug used in a label's `type =` (lowercase)
    name: str          # display name shown in the filter / editor
    color: str         # default text color (a label's own color= still wins)


@dataclass
class RegionConfig:
    """Configuration for a single region"""
    id: str  # e.g., "mojave" (from section name [region.mojave])
    display_name: str
    route_prefix: int
    directory: Path
    enabled_by_default: bool = False
    terrain_tile_dir: Optional[Path] = None  # Path to terrain tiles (.tr4 files)
    track_color: Optional[str] = None  # Per-region track color (hex), falls back to global default

    @property
    def track_database(self) -> Path:
        return self.directory / TRACK_DB_FILENAME

    @property
    def signal_database(self) -> Path:
        return self.directory / SIGNAL_DB_FILENAME

    @property
    def ai_locations_database(self) -> Path:
        return self.directory / AI_LOCATIONS_FILENAME

    @property
    def milepost_database(self) -> Path:
        return self.directory / MILEPOST_DB_FILENAME


@dataclass
class AreaLabel:
    """A user-defined area/place label placed at a tile-local coordinate.

    Positions are stored as a tile index plus Run8 local coordinates within
    that tile, matching how track coordinates are captured. The renderer
    converts these to world meters using the tile_based parameters.
    """
    id: str                          # from section name [area.<id>]
    label: str                       # displayed text
    tile: Tuple[int, int]            # (tile_x, tile_z)
    local: Tuple[float, float]       # (local_x, local_z) Run8 local meters within the tile
    color: Optional[str] = None      # overrides the per-type / global area_label color
    font_size: Optional[int] = None  # overrides the default label font size
    box: bool = False                # draw a background box behind the text
    rotation: float = 0.0            # rotate text in degrees (clockwise), e.g. to align to a track
    type: str = AREA_TYPE_DEFAULT    # category id (must match a [label_types] entry, else rendered white)


@dataclass
class VisualizationConfig:
    """Complete configuration for a multi-region visualization"""
    name: str
    tile_corrections: Path
    region_dir: Path
    output_dir: Path
    regions: List[RegionConfig] = field(default_factory=list)
    colors: ColorConfig = field(default_factory=ColorConfig)
    tile_based: Optional[TileBasedConfig] = None
    initial_center: Optional[Tuple[float, float]] = None  # (lat, lon) for initial map center
    initial_map_opacity: float = 0.2   # [visualization] initial_map_opacity: base-map slider (fraction)
    initial_track_opacity: float = 0.8  # [visualization] initial_track_opacity: track slider (fraction)
    world_save: Optional[Path] = None  # optional Run8 world save (.xml) to plot trains from
    railvehicle_db: Optional[Path] = None  # optional SQLite DB of rail-vehicle lengths
    # ---- track line width (zoom-scaled) ----
    track_width: float = 5.0        # [track] width: full track line width (px) at/above full_zoom
    track_min_width: float = 1.5    # [track] min_width: floor width (px) when zoomed far out
    track_full_zoom: float = 14.0   # [track] full_zoom: zoom at/above which the line shows full
                                    # width; below it the width halves per zoom level down to
                                    # min_width. 0 disables scaling (fixed `width` at every zoom).
    train_car_width: float = 7.0    # [trains] car_width: RV body min line width (px floor)
    train_spine_width: float = 1.5  # [trains] spine_width: train connecting-line width (px)
    train_car_width_m: float = 3.5  # [trains] car_width_m: real RV width (m); RVs widen with
                                    # zoom to this, never below car_width, so they stay wider
                                    # than the track at every zoom. 0 = fixed px (car_width).
    train_label_scale_m: float = 30.0  # [trains] label_scale_m: show per-RV destination tags
                                       # when the scale bar reads this many m or tighter.
    train_label_size: float = 14.0  # [trains] label_size: destination-tag text size (px).
    train_lod_scale_m: float = 300.0  # [trains] lod_scale_m: at/above this scale-bar reading (m)
                                      # trains collapse to a single line (zoom level-of-detail).
    train_lod_min_cars: float = 3.0   # [trains] lod_min_cars: while collapsed, only trains with
                                      # MORE than this many cars are drawn (shorter ones hidden).
    train_moving_hysteresis: int = 2  # [trains] moving_hysteresis: keep a train flagged "moving"
                                      # this many stationary save-cycles after its last real
                                      # movement, so brief holds (e.g. at a signal) don't flicker
                                      # off the highlight. 0 = strict per-cycle. serve.py live only.
    train_deoverlap: bool = True    # [trains] deoverlap: lay each consist's cars end-to-end
                                    # (front->back XML order, DB length, midpoint anchor) so
                                    # coupled cars don't overlap. False = raw per-truck placement.
    areas: List[AreaLabel] = field(default_factory=list)  # user-defined area/place labels
    color_presets: Dict[str, str] = field(
        default_factory=lambda: dict(DEFAULT_COLOR_PRESETS))  # name -> hex label-color palette
    label_types: List[LabelType] = field(default_factory=list)  # from [label_types], ordered
    car_type_colors: Dict[str, str] = field(
        default_factory=dict)  # INDUSTRY_CONFIG_CAR_TYPE (lowercased) -> hex, from [car_type_colors]
    loco_company_colors: Dict[str, str] = field(
        default_factory=dict)  # loco reporting mark / INITIAL (lowercased) -> hex, from [loco_company_colors]

    @property
    def industry_db(self) -> Path:
        """Path to industry database file (derived from region_dir)"""
        return self.region_dir / INDUSTRY_DB_FILENAME

    @property
    def terrain_tile_dir(self) -> Path:
        """Path to terrain tiles directory (derived from region_dir)"""
        return self.region_dir / TERRAIN_DIR_NAME


class ConfigError(Exception):
    """Raised when configuration is invalid"""
    pass


def _parse_area_sections(parser: configparser.ConfigParser, source: str, errors: List[str]) -> List['AreaLabel']:
    """Parse all [area.*] sections from a parser into AreaLabel objects.

    Appends any problems to `errors`; `source` describes the origin (e.g. the
    config or an areas_file path) for clearer messages. Shared by the main config
    and any external areas_file so both accept identical syntax.
    """
    result = []
    for section_name in (s for s in parser.sections() if s.startswith('area.')):
        area_id = section_name[5:]  # Remove "area." prefix
        area = parser[section_name]

        label = area.get('label', '').strip()
        if not label:
            errors.append(f"[{section_name}] label is required ({source})")

        tile_str = area.get('tile', '').strip()
        tile = None
        if not tile_str:
            errors.append(f"[{section_name}] tile is required, format 'x,z' ({source})")
        else:
            try:
                tp = tile_str.split(',')
                tile = (int(tp[0].strip()), int(tp[1].strip()))
            except (ValueError, IndexError):
                errors.append(f"[{section_name}] tile must be format 'x,z' e.g. '209,-10' ({source})")

        local_str = area.get('local', '').strip()
        local = None
        if not local_str:
            errors.append(f"[{section_name}] local is required, format 'x,z' ({source})")
        else:
            try:
                lp = local_str.split(',')
                local = (float(lp[0].strip()), float(lp[1].strip()))
            except (ValueError, IndexError):
                errors.append(f"[{section_name}] local must be format 'x,z' e.g. '421.5,-500.2' ({source})")

        # Store the raw value (preset name or hex); resolved to hex at render time.
        color = area.get('color', '').strip() or None

        font_size_str = area.get('font_size', '').strip()
        font_size = None
        if font_size_str:
            try:
                font_size = int(font_size_str)
            except ValueError:
                errors.append(f"[{section_name}] font_size must be an integer ({source})")

        box = area.get('box', 'false').strip().lower() in ('true', 'yes', '1')

        rotation_str = area.get('rotation', '').strip()
        rotation = 0.0
        if rotation_str:
            try:
                rotation = float(rotation_str)
            except ValueError:
                errors.append(f"[{section_name}] rotation must be a number in degrees ({source})")

        # Any type string is accepted; ones not defined in [label_types] render white.
        area_type = area.get('type', '').strip().lower() or AREA_TYPE_DEFAULT

        if label and tile is not None and local is not None:
            result.append(AreaLabel(
                id=area_id,
                label=label,
                tile=tile,
                local=local,
                color=color,
                font_size=font_size,
                box=box,
                rotation=rotation,
                type=area_type
            ))
    return result


def parse_config(config_path: str) -> VisualizationConfig:
    """
    Parse a multi-region configuration file.

    Args:
        config_path: Path to the INI configuration file

    Returns:
        VisualizationConfig with all parsed settings

    Raises:
        ConfigError: If configuration is invalid or files are missing
    """
    config_file = Path(config_path)
    if not config_file.exists():
        raise ConfigError(f"Configuration file not found: {config_path}")

    parser = configparser.ConfigParser()
    parser.read(config_path)

    errors = []
    areas_file_str = ''  # optional; comma-separated external areas file(s)
    initial_map_opacity = 0.2   # [visualization] initial_map_opacity (base map)
    initial_track_opacity = 0.8  # [visualization] initial_track_opacity (track vectors)

    # Parse [visualization] section
    if 'visualization' not in parser:
        errors.append("[visualization] section is required")
    else:
        viz = parser['visualization']
        name = viz.get('name', '').strip()
        if not name:
            errors.append("[visualization] name is required")

        # Optional external area-label file(s), comma-separated, resolved
        # relative to this config's directory.
        areas_file_str = viz.get('areas_file', '').strip()

        tile_corrections_str = viz.get('tile_corrections', '').strip()
        if not tile_corrections_str:
            errors.append("[visualization] tile_corrections is required")

        region_dir_str = viz.get('region_dir', '').strip()
        if not region_dir_str:
            errors.append("[visualization] region_dir is required")

        # Optional world_save: a Run8 world save (.xml) to plot trains from.
        # A --world CLI switch overrides this; resolved relative to the config dir.
        world_save_str = viz.get('world_save', '').strip()

        # Optional railvehicle_db: SQLite DB (db_railvehicles.db) mapping
        # rvXMLfilename -> RV_LENGTH, used to draw true vehicle length over the
        # trucks. Resolved relative to the config dir.
        railvehicle_db_str = viz.get('railvehicle_db', '').strip()

        # Optional initial_center (format: "lat,lon" e.g., "34.9,-118.0")
        initial_center_str = viz.get('initial_center', '').strip()
        initial_center = None
        if initial_center_str:
            try:
                parts = initial_center_str.split(',')
                initial_center = (float(parts[0].strip()), float(parts[1].strip()))
            except (ValueError, IndexError):
                errors.append(f"[visualization] initial_center must be in format 'lat,lon' (e.g., '34.9,-118.0')")

        # Optional initial opacity slider values. Accept a percent (0-100) or a
        # fraction (0-1); stored as a fraction. Defaults: map 0.2, track 0.8.
        def _opacity(key, default):
            s = viz.get(key, '').split(';', 1)[0].split('#', 1)[0].strip()
            if not s:
                return default
            try:
                v = float(s)
            except ValueError:
                errors.append(f"[visualization] {key} must be a number (0-100)")
                return default
            if v > 1:
                v /= 100.0
            return max(0.0, min(1.0, v))
        initial_map_opacity = _opacity('initial_map_opacity', 0.2)
        initial_track_opacity = _opacity('initial_track_opacity', 0.8)

    # Parse [output] section
    if 'output' not in parser:
        errors.append("[output] section is required")
    else:
        out = parser['output']
        output_dir_str = out.get('output_dir', '').strip()
        if not output_dir_str:
            errors.append("[output] output_dir is required")

    # Parse [region.*] sections
    region_sections = [s for s in parser.sections() if s.startswith('region.')]
    if not region_sections:
        errors.append("At least one [region.*] section is required")

    regions = []
    for section_name in region_sections:
        region_id = section_name[7:]  # Remove "region." prefix
        region = parser[section_name]

        display_name = region.get('display_name', '').strip()
        if not display_name:
            errors.append(f"[{section_name}] display_name is required")

        route_prefix_str = region.get('route_prefix', '').strip()
        if not route_prefix_str:
            errors.append(f"[{section_name}] route_prefix is required")
        else:
            try:
                route_prefix = int(route_prefix_str)
            except ValueError:
                errors.append(f"[{section_name}] route_prefix must be an integer")
                route_prefix = 0

        directory_str = region.get('directory', '').strip()
        if not directory_str:
            errors.append(f"[{section_name}] directory is required")

        enabled_str = region.get('enabled_by_default', 'false').strip().lower()
        enabled_by_default = enabled_str in ('true', 'yes', '1')

        # Optional terrain tile directory
        terrain_tile_dir_str = region.get('terrain_tile_dir', '').strip()
        terrain_tile_dir = Path(terrain_tile_dir_str) if terrain_tile_dir_str else None

        # Optional per-region track color
        track_color_str = region.get('track_color', '').strip()
        track_color = track_color_str if track_color_str else None

        if display_name and directory_str:
            regions.append(RegionConfig(
                id=region_id,
                display_name=display_name,
                route_prefix=route_prefix,
                directory=Path(directory_str),
                enabled_by_default=enabled_by_default,
                terrain_tile_dir=terrain_tile_dir,
                track_color=track_color
            ))

    # Parse [area.*] sections: inline in this config, plus any external file(s)
    # listed in [visualization] areas_file (comma-separated, resolved relative to
    # the config's directory). Both sources use identical [area.*] syntax.
    areas = _parse_area_sections(parser, 'config', errors)

    config_dir = config_file.parent
    for area_path_str in (p.strip() for p in areas_file_str.split(',') if p.strip()):
        area_path = Path(area_path_str)
        if not area_path.is_absolute():
            area_path = config_dir / area_path
        if not area_path.exists():
            errors.append(f"[visualization] areas_file not found: {area_path}")
            continue
        ext_parser = configparser.ConfigParser()
        try:
            ext_parser.read(area_path)
        except configparser.Error as e:
            errors.append(f"[visualization] areas_file could not be parsed ({area_path}): {e}")
            continue
        areas.extend(_parse_area_sections(ext_parser, area_path.name, errors))

    # Reject duplicate area ids across all sources (avoids silent overrides)
    seen_area_ids = set()
    for a in areas:
        if a.id in seen_area_ids:
            errors.append(f"Duplicate area id '{a.id}' (defined more than once across config and areas_file)")
        seen_area_ids.add(a.id)

    # If we have basic parsing errors, raise now
    if errors:
        raise ConfigError("Configuration errors:\n  - " + "\n  - ".join(errors))

    # Parse [colors] section (optional - use defaults if not present)
    colors = ColorConfig()
    if 'colors' in parser:
        color_section = parser['colors']

        # Strip an inline ';' comment (the default ConfigParser keeps it in the
        # value, which would bake a bad CSS colour). '#' is the hex prefix, so it
        # is NOT a comment char here.
        def _c(key, default):
            return color_section.get(key, default).split(';', 1)[0].strip()

        colors = ColorConfig(
            track=_c('track', colors.track),
            track_selected=_c('track_selected', colors.track_selected),
            track_hover=_c('track_hover', colors.track_hover),
            switch=_c('switch', colors.switch),
            industry_track=_c('industry_track', colors.industry_track),
            signal_absolute=_c('signal_absolute', colors.signal_absolute),
            signal_intermediate=_c('signal_intermediate', colors.signal_intermediate),
            signal_border_single=_c('signal_border_single', colors.signal_border_single),
            signal_border_stacked=_c('signal_border_stacked', colors.signal_border_stacked),
            background=_c('background', colors.background),
            area_label=_c('area_label', colors.area_label),
            train=_c('train', colors.train),
            train_loco=_c('train_loco', colors.train_loco),
            train_outline=_c('train_outline', colors.train_outline),
        )

    # Parse [trains] section (optional): rail-vehicle rendering line widths.
    # Strip inline ';'/'#' comments ourselves: the default ConfigParser keeps them
    # in the value, which would break float().
    def _cfg_float(section, key, default):
        raw = section.get(key)
        if raw is None:
            return default
        s = raw.split(';', 1)[0].split('#', 1)[0].strip()
        if not s:
            return default
        try:
            return float(s)
        except ValueError:
            errors.append(f"[trains] {key} must be a number (got {raw!r})")
            return default

    train_car_width, train_spine_width, train_car_width_m = 7.0, 1.5, 3.5
    train_label_scale_m = 30.0
    train_label_size = 14.0
    train_lod_scale_m = 300.0
    train_lod_min_cars = 3.0
    train_moving_hysteresis = 2
    train_deoverlap = True
    if 'trains' in parser:
        ts = parser['trains']
        train_car_width = _cfg_float(ts, 'car_width', train_car_width)
        train_spine_width = _cfg_float(ts, 'spine_width', train_spine_width)
        train_car_width_m = _cfg_float(ts, 'car_width_m', train_car_width_m)
        train_label_scale_m = _cfg_float(ts, 'label_scale_m', train_label_scale_m)
        train_label_size = _cfg_float(ts, 'label_size', train_label_size)
        train_lod_scale_m = _cfg_float(ts, 'lod_scale_m', train_lod_scale_m)
        train_lod_min_cars = _cfg_float(ts, 'lod_min_cars', train_lod_min_cars)
        train_moving_hysteresis = int(_cfg_float(ts, 'moving_hysteresis', train_moving_hysteresis))
        _do = ts.get('deoverlap', '').split(';', 1)[0].split('#', 1)[0].strip().lower()
        if _do:
            train_deoverlap = _do in ('1', 'true', 'yes', 'on')

    # Parse [track] section (optional): zoom-scaled track line width.
    track_width, track_min_width, track_full_zoom = 5.0, 1.5, 14.0
    if 'track' in parser:
        tk = parser['track']
        track_width = _cfg_float(tk, 'width', track_width)
        track_min_width = _cfg_float(tk, 'min_width', track_min_width)
        track_full_zoom = _cfg_float(tk, 'full_zoom', track_full_zoom)

    # Parse [car_type_colors] (optional): INDUSTRY_CONFIG_CAR_TYPE -> hex colour for
    # the RV body. configparser lower-cases keys, so match car types case-insensitively.
    car_type_colors = {}
    if 'car_type_colors' in parser:
        for ctype, hexcolor in parser['car_type_colors'].items():
            c = hexcolor.split(';', 1)[0].strip()   # strip ';' inline comment ('#' is the hex prefix)
            if c:
                if not c.startswith('#'):
                    c = '#' + c
                car_type_colors[ctype.strip().lower()] = c

    # Parse [loco_company_colors] (optional): loco reporting mark (INITIAL) -> hex
    # colour, for colouring locomotives by owning railroad. configparser lower-cases
    # keys, so match reporting marks case-insensitively (viewer lowercases too).
    loco_company_colors = {}
    if 'loco_company_colors' in parser:
        for mark, hexcolor in parser['loco_company_colors'].items():
            c = hexcolor.split(';', 1)[0].strip()   # strip ';' inline comment ('#' is the hex prefix)
            if c:
                if not c.startswith('#'):
                    c = '#' + c
                loco_company_colors[mark.strip().lower()] = c

    # Parse [label_types] section (optional): `id = Display Name, #color` per line,
    # in order. The id is the value stored in a label's `type =`; the display name
    # shows in the filter/editor; the color is the category default (a label's own
    # color= still wins). Missing color -> undefined/white; missing name -> id.
    label_types = []
    if 'label_types' in parser:
        for tid, raw in parser['label_types'].items():
            t_name, t_color = raw, ''
            if ',' in raw:
                t_name, t_color = raw.rsplit(',', 1)
            t_name = t_name.strip() or tid.strip()
            t_color = t_color.strip() or UNDEFINED_TYPE_COLOR
            label_types.append(LabelType(id=tid.strip().lower(), name=t_name, color=t_color))

    # Parse [color_presets] section (optional): name = hex, merged over the built-ins.
    color_presets = dict(DEFAULT_COLOR_PRESETS)
    if 'color_presets' in parser:
        for preset_name, value in parser['color_presets'].items():
            hexval = (value or '').strip()
            if hexval:
                color_presets[preset_name.strip().lower()] = hexval

    # Parse [tile_based_plot] section (optional)
    tile_based = None
    if 'tile_based_plot' in parser:
        tb_section = parser['tile_based_plot']

        # Parse home_tile (format: "x,z" e.g., "209,-10")
        home_tile_str = tb_section.get('home_tile', '0,0').strip()
        try:
            parts = home_tile_str.split(',')
            home_tile = (int(parts[0].strip()), int(parts[1].strip()))
        except (ValueError, IndexError):
            errors.append(f"[tile_based_plot] home_tile must be in format 'x,z' (e.g., '209,-10')")
            home_tile = (0, 0)

        tile_width = float(tb_section.get('tile_width', '842.3').strip())
        tile_height = float(tb_section.get('tile_height', '1023.2').strip())

        tile_based = TileBasedConfig(
            home_tile=home_tile,
            tile_width=tile_width,
            tile_height=tile_height
        )

    # Build the config object
    config = VisualizationConfig(
        name=name,
        tile_corrections=Path(tile_corrections_str),
        region_dir=Path(region_dir_str),
        output_dir=Path(output_dir_str),
        regions=regions,
        colors=colors,
        tile_based=tile_based,
        initial_center=initial_center,
        initial_map_opacity=initial_map_opacity,
        initial_track_opacity=initial_track_opacity,
        world_save=(config_file.parent / world_save_str) if world_save_str else None,
        railvehicle_db=(config_file.parent / railvehicle_db_str) if railvehicle_db_str else None,
        train_car_width=train_car_width,
        train_spine_width=train_spine_width,
        train_car_width_m=train_car_width_m,
        train_label_scale_m=train_label_scale_m,
        train_label_size=train_label_size,
        train_lod_scale_m=train_lod_scale_m,
        train_lod_min_cars=train_lod_min_cars,
        train_moving_hysteresis=train_moving_hysteresis,
        train_deoverlap=train_deoverlap,
        track_width=track_width,
        track_min_width=track_min_width,
        track_full_zoom=track_full_zoom,
        car_type_colors=car_type_colors,
        loco_company_colors=loco_company_colors,
        areas=areas,
        color_presets=color_presets,
        label_types=label_types
    )

    # Validate that all files exist
    file_errors = []

    if not config.tile_corrections.exists():
        file_errors.append(f"[visualization] tile_corrections file not found: {config.tile_corrections}")

    if not config.region_dir.exists():
        file_errors.append(f"[visualization] region_dir not found: {config.region_dir}")
    else:
        # Validate derived paths
        if not config.industry_db.exists():
            file_errors.append(f"[visualization] industry_db not found: {config.industry_db} (derived from region_dir)")
        if not config.terrain_tile_dir.exists():
            file_errors.append(f"[visualization] terrain tile directory not found: {config.terrain_tile_dir} (derived from region_dir)")

    for region in config.regions:
        if not region.directory.exists():
            file_errors.append(f"[region.{region.id}] directory not found: {region.directory}")
        else:
            # Check for required files in directory
            if not region.track_database.exists():
                file_errors.append(f"[region.{region.id}] track database not found: {region.track_database}")
            if not region.signal_database.exists():
                # Signal database is optional (dark territory regions have no signals)
                print(f"  Note: [region.{region.id}] has no signal database (dark territory) - signals will not be rendered")
            if not region.ai_locations_database.exists():
                # AI locations database is optional
                print(f"  Note: [region.{region.id}] has no AI locations database - AI locations will not be rendered")

        # Validate terrain_tile_dir if specified
        if region.terrain_tile_dir and not region.terrain_tile_dir.exists():
            file_errors.append(f"[region.{region.id}] terrain_tile_dir not found: {region.terrain_tile_dir}")

    if file_errors:
        raise ConfigError("Missing files:\n  - " + "\n  - ".join(file_errors))

    return config


def parse_areas_file(path) -> List[AreaLabel]:
    """Parse the [area.*] sections from a single INI file into AreaLabels.

    Lightweight (no file/region validation) so it can be reused by the live
    authoring server (serve.py) to read the current labels in an areas file.
    Raises ConfigError if any [area.*] section is malformed.
    """
    path = Path(path)
    parser = configparser.ConfigParser()
    parser.read(path)
    errors: List[str] = []
    areas = _parse_area_sections(parser, path.name, errors)
    if errors:
        raise ConfigError("Area file errors:\n  - " + "\n  - ".join(errors))
    return areas


def resolve_areas_files(config_path) -> List[Path]:
    """Return the absolute paths of the external areas_file(s) named in a config.

    Mirrors parse_config's resolution: comma-separated, relative to the config
    directory. Does not check existence. Returns [] if none are configured.
    """
    config_file = Path(config_path)
    parser = configparser.ConfigParser()
    parser.read(config_file)
    areas_file_str = ''
    if 'visualization' in parser:
        areas_file_str = parser['visualization'].get('areas_file', '').strip()
    config_dir = config_file.parent
    paths: List[Path] = []
    for area_path_str in (p.strip() for p in areas_file_str.split(',') if p.strip()):
        area_path = Path(area_path_str)
        if not area_path.is_absolute():
            area_path = config_dir / area_path
        paths.append(area_path)
    return paths


def collect_areas(config_path) -> List[AreaLabel]:
    """Merge all [area.*] labels for a config: inline plus every areas_file.

    Matches parse_config's merge order (inline first, then each areas_file in
    order) but performs no region/file validation, so the live authoring server
    can recompute the authoritative label set cheaply after each edit. Missing
    areas_file paths are skipped silently (an empty/absent file = no labels).
    """
    config_file = Path(config_path)
    parser = configparser.ConfigParser()
    parser.read(config_file)
    errors: List[str] = []
    areas = _parse_area_sections(parser, 'config', errors)
    for area_path in resolve_areas_files(config_file):
        if not area_path.exists():
            continue
        ext_parser = configparser.ConfigParser()
        ext_parser.read(area_path)
        areas.extend(_parse_area_sections(ext_parser, area_path.name, errors))
    if errors:
        raise ConfigError("Area errors:\n  - " + "\n  - ".join(errors))
    return areas


def generate_sample_config() -> str:
    """Generate a sample configuration file template"""
    return '''# Multi-Region Track Mapper Configuration
#
# This file defines a visualization containing multiple regions.

[visualization]
# Display name for the visualization
name = Southern California

# Path to tile corrections CSV (global for all regions)
tile_corrections = tile_corrections_socal.csv

# Path to the region directory containing Config.ind and TerrainTiles
# The industry database (Config.ind) and terrain tiles directory (TerrainTiles)
# are automatically derived from this path
region_dir = C:\\Run8Studios\\Run8 Train Simulator V3\\Content\\V3Routes\\Regions\\SouthernCA

# (Optional) Initial map center as lat,lon (e.g., 34.9,-118.0)
# If not specified, uses a default center
# initial_center = 34.9,-118.0

# (Optional) External area-label file(s), comma-separated, resolved relative to
# this config's directory. Use when you have many labels and don't want them
# cluttering this file. Same [area.*] syntax as the inline example below; inline
# and external labels are merged (duplicate ids are rejected).
# areas_file = areas_socal.ini
# areas_file = yards.ini, towns.ini

[region.mojave]
# Human-readable name for this region
display_name = Mojave Subdivision

# Route prefix integer for filtering industries
route_prefix = 1

# Directory containing TrackDatabase.r8, SignalHeadDatabase.r8, AiSpecialLocations.r8
directory = C:\\Run8Studios\\Run8 Train Simulator V3\\Content\\V3Routes\\BNSF_MojaveSub

# (Optional) Path to terrain tile directory containing .tr4 files
# If not specified, uses the default SouthernCA tile location
terrain_tile_dir = C:\\Run8Studios\\Run8 Train Simulator V3\\Content\\V3Routes\\Regions\\SouthernCA\\TerrainTiles

# Whether this region is enabled by default when the map loads
enabled_by_default = true

# (Optional) Per-region track color - overrides the global track color
# track_color = #0066cc

[region.barstow]
display_name = Barstow Subdivision
route_prefix = 2
directory = C:\\Run8Studios\\Run8 Train Simulator V3\\Content\\V3Routes\\BNSF_BarstowSub
# terrain_tile_dir can be omitted to use the default location
enabled_by_default = false

# (Optional) Per-region track color - overrides the global track color
# track_color = #2266ff

# Add more [region.*] sections as needed...

[colors]
# All colors are optional - defaults are shown below
# Colors can be hex (#RRGGBB) or named colors

# Track colors
track = #0066cc
track_selected = #ff0000
track_hover = #ffff00
switch = #800080
industry_track = #00aa00

# Signal colors
signal_absolute = #FF6B35
signal_intermediate = #FFD700
signal_border_single = #000000
signal_border_stacked = #87CEEB

# Default color for area/place labels (see [area.*] below)
# area_label = #ffd11a

# User-defined area/place labels (optional, tile-based mode).
# Each [area.<id>] draws its text at a tile + local coordinate.
# Tip: Shift+Click the rendered map to capture tile/local coords and get a
# ready-to-paste [area.*] block.
#
# [area.barstow_yard]
# label = Barstow Yard
# tile = 209,-10           ; tile_x,tile_z
# local = 421.5,-500.2     ; Run8 local_x,local_z within that tile
# color = #ffd11a          ; optional, overrides area_label
# font_size = 16           ; optional
# box = true               ; optional, draw a background box behind the text (default: no box)
# rotation = -30           ; optional, rotate text in degrees (clockwise) to align to a track/yard

[output]
# Directory where output files will be written
output_dir = ./output/socal/
'''


if __name__ == '__main__':
    import sys

    if len(sys.argv) < 2:
        print("Usage: config_parser.py <config.ini>")
        print("       config_parser.py --generate")
        sys.exit(1)

    if sys.argv[1] == '--generate':
        print(generate_sample_config())
    else:
        try:
            config = parse_config(sys.argv[1])
            print(f"Configuration loaded successfully!")
            print(f"  Name: {config.name}")
            print(f"  Tile corrections: {config.tile_corrections}")
            print(f"  Region dir: {config.region_dir}")
            print(f"  Industry DB (derived): {config.industry_db}")
            print(f"  Terrain tiles (derived): {config.terrain_tile_dir}")
            print(f"  Output directory: {config.output_dir}")
            print(f"  Regions: {len(config.regions)}")
            for region in config.regions:
                print(f"    - {region.display_name} (prefix={region.route_prefix}, enabled={region.enabled_by_default})")
                print(f"      Directory: {region.directory}")
                if region.terrain_tile_dir:
                    print(f"      Terrain tiles: {region.terrain_tile_dir}")
        except ConfigError as e:
            print(f"ERROR: {e}")
            sys.exit(1)
