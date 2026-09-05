#!/usr/bin/env python3
"""
Region data extraction module for Run8 Track Mapper.

Extracts track sections, signals, AI locations, and industries from
Run8 database files and converts them to JSON-serializable format.
"""

import csv
import math
import os
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# Import existing parsers
from explore_trackdb import TrackDatabase, TrackSection, TrackNode
from explore_signalHeadDb import SignalDatabase, SignalHead
from r8lib import SpawnFile, SpawnPoint, IndustryFile, Industry

from config_parser import RegionConfig, VisualizationConfig, TileBasedConfig


# Constants for coordinate conversion
METERS_PER_DEGREE_LAT = 111139.0

# Run8 local tile size in meters (SW corner is local (0, 0); x increases east,
# z decreases going north). Used to normalise local coords into a [0, 1]
# fraction across a tile for bilinear lat/lon interpolation. Matches the
# [tile_based_plot] defaults; kept here so geographic mode does not require a
# TileBasedConfig. Override via convert_run8_to_latlon's tile_width/tile_height.
LOCAL_TILE_WIDTH_M = 842.3
LOCAL_TILE_HEIGHT_M = 1023.2

# Binary field widths (little-endian) used by the milepost parser
INTLEN = 4
FLTLEN = 4

# Coordinate bounds for validation (CONUS)
LON_MIN, LON_MAX = -130.0, -65.0
LAT_MIN, LAT_MAX = 23.0, 50.0

# AI spawn point type names
SPAWN_TYPE_NAMES = {
    0: "Spawn Point",
    1: "Crew Change",
    2: "Crew Change & Hold",
    3: "Passenger",
    4: "Passenger Crew Change",
    5: "Passenger Crew Change & Hold",
    6: "Relinquish",
    7: "Passenger Relinquish"
}


@dataclass
class SectionData:
    """Extracted track section data ready for JSON serialization"""
    id: int
    paths: List[List[Tuple[float, float]]]  # List of paths, each path is a list of (lat, lon) tuples
    length_ft: float
    length_m: float
    is_switch: bool = False
    track_type: int = 0
    retarder_mph: float = -1.0
    elevation_start_m: float = 0.0
    elevation_end_m: float = 0.0


@dataclass
class SignalData:
    """Extracted signal data ready for JSON serialization"""
    id: int
    lat: float
    lon: float
    rotation: float
    signal_type: str  # "absolute" or "intermediate"
    name: str
    model_name: str = ""
    is_dwarf: bool = False
    is_switch_indicator: bool = False
    is_advance_diverging: bool = False
    stacked_ids: List[int] = None  # All signal IDs in this stack (for multi-head signals)


@dataclass
class AILocationData:
    """Extracted AI location data ready for JSON serialization"""
    id: int
    name: str
    lat: float
    lon: float
    type_id: int
    type_name: str


@dataclass
class MilepostData:
    """Extracted milepost/marker data ready for JSON serialization"""
    id: int
    label: str        # display label (station name and/or mile value)
    milepost: str     # raw mile value string (may be empty)
    station: str      # raw station/marker name string (may be empty or "MP" placeholder)
    lat: float
    lon: float
    elevation_m: float = 0.0


@dataclass
class IndustryData:
    """Extracted industry data ready for JSON serialization"""
    tag: str
    name: str
    local_name: str  # Local train symbol that services this industry
    lat: float
    lon: float
    track_sections: List[int] = field(default_factory=list)  # List of section IDs this industry occupies


@dataclass
class TileData:
    """Tile boundary data for visualization"""
    x: int
    z: int
    lat_north: float
    lat_south: float
    lon_east: float
    lon_west: float
    is_corrected: bool = False


@dataclass
class RegionData:
    """Complete extracted data for a single region"""
    id: str
    display_name: str
    sections: List[SectionData] = field(default_factory=list)
    signals: List[SignalData] = field(default_factory=list)
    ai_locations: List[AILocationData] = field(default_factory=list)
    mileposts: List[MilepostData] = field(default_factory=list)
    industries: List[IndustryData] = field(default_factory=list)
    tiles: List[TileData] = field(default_factory=list)
    trains: List["TrainData"] = field(default_factory=list)  # from an optional world save
    bounds: Optional[Tuple[Tuple[float, float], Tuple[float, float]]] = None  # ((min_lat, min_lon), (max_lat, max_lon))


def decompress_tr4(filepath: str) -> Optional[bytes]:
    """Decompress a TR4 terrain tile file"""
    try:
        with open(filepath, 'rb') as f:
            compressed_data = f.read()
        return zlib.decompress(compressed_data, -15)
    except Exception as e:
        print(f"  Error decompressing {filepath}: {e}")
        return None


def _extract_coords_at_offset(data: bytes, offset: int) -> Optional[Tuple[float, float, float, float]]:
    """Extract and validate coordinates at a specific offset.

    Returns tuple (lon_east, lon_west, lat_north, lat_south) if valid, None otherwise.
    Uses strict tile size validation (0.005° - 0.02°) to filter false positives.
    """
    try:
        if offset + 16 > len(data):
            return None
        lon_east = struct.unpack('<f', data[offset:offset+4])[0]
        lon_west = struct.unpack('<f', data[offset+4:offset+8])[0]
        lat_north = struct.unpack('<f', data[offset+8:offset+12])[0]
        lat_south = struct.unpack('<f', data[offset+12:offset+16])[0]

        # Validate coordinates with strict tile size bounds
        # Typical tile size is ~0.009° × 0.009°
        if (-180 < lon_east < 0 and -180 < lon_west < 0 and
            0 < lat_north < 90 and 0 < lat_south < 90 and
            lon_west < lon_east and lat_south < lat_north and
            0.005 < abs(lon_east - lon_west) < 0.02 and
            0.005 < abs(lat_north - lat_south) < 0.02):
            return (lon_east, lon_west, lat_north, lat_south)
    except:
        pass
    return None


def find_tile_bounds(data: bytes, offset_e: float = 0.0, offset_n: float = 0.0) -> Optional[Tuple[float, float, float, float]]:
    """Find geographic bounds in TR4 file data.

    Uses a multi-method approach for reliability:
    1. Sentinel-based: Look for uint32 sentinel value 3, coordinates at +40 bytes
    2. Direct access with string detection: Look for coordinates followed by known strings
    3. Direct access best coordinates: Score all valid coordinates and pick the best

    Returns: (lon_east, lon_west, lat_north, lat_south) or None
    """
    # Method 1: Try sentinel-3 based parsing
    for offset in range(0, len(data) - 44):
        try:
            sentinel = struct.unpack('<I', data[offset:offset+4])[0]
            if sentinel == 3:
                coord_offset = offset + 40
                coords = _extract_coords_at_offset(data, coord_offset)
                if coords:
                    # Apply offsets
                    return (coords[0] - offset_e, coords[1] - offset_e,
                            coords[2] - offset_n, coords[3] - offset_n)
        except:
            pass

    # Method 2: Direct access with string pattern detection
    # Look for coordinates followed by known terrain/vegetation strings
    known_strings = [b'NoCalVeg01', b'NSF_Mojave', b'NSF_Sierra', b'ProcVeg01']

    for offset in range(0, len(data) - 16):
        coords = _extract_coords_at_offset(data, offset)
        if coords:
            # Check if followed by known string pattern (within next 64 bytes)
            search_end = min(offset + 80, len(data))
            chunk = data[offset + 16:search_end]
            for known_str in known_strings:
                if known_str in chunk:
                    # Apply offsets
                    return (coords[0] - offset_e, coords[1] - offset_e,
                            coords[2] - offset_n, coords[3] - offset_n)

    # Method 3: Direct access - find ALL valid coordinates and choose the best one
    # This avoids false positives by scoring candidates on geographic plausibility
    valid_coords = []
    for offset in range(0, len(data) - 16):
        coords = _extract_coords_at_offset(data, offset)
        if coords:
            valid_coords.append(coords)

    if valid_coords:
        # Choose the best coordinates based on geographic plausibility
        best_score = -1
        best_coords = None

        for coords in valid_coords:
            lon_east, lon_west, lat_north, lat_south = coords
            score = 0

            # Prefer realistic latitude/longitude ranges (California region)
            if -130 <= lon_east <= -110:
                score += 10
            if 30 <= lat_north <= 40:
                score += 10

            # Prefer reasonable tile sizes
            tile_width = abs(lon_east - lon_west)
            tile_height = abs(lat_north - lat_south)
            if 0.005 <= tile_width <= 0.015 and 0.005 <= tile_height <= 0.015:
                score += 5

            # Avoid coordinates near zero (likely false positives)
            if abs(lon_east) < 1 and abs(lat_north) < 1:
                score -= 20

            if score > best_score:
                best_score = score
                best_coords = coords

        if best_coords:
            # Apply offsets
            return (best_coords[0] - offset_e, best_coords[1] - offset_e,
                    best_coords[2] - offset_n, best_coords[3] - offset_n)

    return None


def load_tile_corrections(csv_path: str) -> Dict[Tuple[int, int], Dict[str, float]]:
    """Load tile corrections from CSV file

    Returns: Map of (tile_x, tile_z) -> {'lat_offset': float, 'lon_offset': float}
    """
    corrections = {}

    if not os.path.exists(csv_path):
        return corrections

    try:
        with open(csv_path, 'r', newline='') as f:
            reader = csv.DictReader(f)
            for row in reader:
                tile_x = int(row['tile_x'])
                tile_z = int(row['tile_z'])
                corrections[(tile_x, tile_z)] = {
                    'lat_offset': float(row['lat_offset']),
                    'lon_offset': float(row['lon_offset'])
                }
        print(f"  Loaded {len(corrections)} tile correction(s)")
    except Exception as e:
        print(f"  Warning: Failed to load tile corrections: {e}")

    return corrections


def load_tile_bounds(tiles_involved: Set[Tuple[int, int]],
                     tile_corrections: Dict[Tuple[int, int], Dict[str, float]],
                     tile_dir: str) -> Tuple[Dict, Set]:
    """Load geographic bounds for all tiles involved

    Returns: (tile_geo_bounds dict, corrected_tiles set)
    """
    tile_geo_bounds = {}
    corrected_tiles = set()

    for tile_coords in sorted(tiles_involved):
        x_str = f"{tile_coords[0]:06d}" if tile_coords[0] < 0 else f"{tile_coords[0]:05d}"
        y_str = f"{tile_coords[1]:06d}" if tile_coords[1] < 0 else f"{tile_coords[1]:05d}"
        filename = f"{x_str}_{y_str}.tr4"
        filepath = os.path.join(tile_dir, filename)

        if not os.path.exists(filepath):
            print(f"  Warning: No such file: {filepath}")
            continue

        data = decompress_tr4(filepath)
        if data is None:
            continue

        bounds = find_tile_bounds(data)
        if not bounds:
            continue

        # Apply corrections if available
        if tile_coords in tile_corrections:
            correction = tile_corrections[tile_coords]
            lon_east = bounds[0] + correction['lon_offset']
            lon_west = bounds[1] + correction['lon_offset']
            lat_north = bounds[2] + correction['lat_offset']
            lat_south = bounds[3] + correction['lat_offset']
            bounds = (lon_east, lon_west, lat_north, lat_south)
            corrected_tiles.add(tile_coords)

        tile_geo_bounds[tile_coords] = bounds

    return tile_geo_bounds, corrected_tiles


def convert_run8_to_latlon(x: float, z: float,
                           tile_geo_bounds: Tuple[float, float, float, float],
                           tile_width: float = LOCAL_TILE_WIDTH_M,
                           tile_height: float = LOCAL_TILE_HEIGHT_M) -> Tuple[float, float]:
    """Convert Run8 tile-local coordinates to lat/lon by bilinear interpolation
    across the tile's four real corners.

    ``x``/``z`` are Run8 local coordinates within the tile: the SW corner is
    (0, 0), x increases east, and z decreases (goes negative) moving north.
    The point is placed by its fractional position across the tile, so every
    real corner is honoured exactly and adjacent tiles - which tessellate to
    ~0 m in the .tr4 data - stay continuous across their shared edge. For a
    point just past an edge the fraction falls slightly outside [0, 1] and is
    linearly extrapolated into the neighbour, which is accurate because
    neighbouring tiles share that edge.

    This replaces the old SW-corner-anchored conversion, which used a single
    assumed metres-per-degree scale and so drifted from the real geography
    (validated against OSM: bilinear lands within ~10 m of real rails).
    """
    lon_east, lon_west, lat_north, lat_south = tile_geo_bounds

    frac_x = x / tile_width        # 0 at west edge, 1 at east edge
    frac_y = -z / tile_height      # 0 at south edge, 1 at north edge (z < 0 north)

    lat = lat_south + frac_y * (lat_north - lat_south)
    lon = lon_west + frac_x * (lon_east - lon_west)

    return (lat, lon)


def convert_run8_to_tile_coords(
    x: float, z: float,
    tile_index: Tuple[int, int],
    home_tile: Tuple[int, int],
    tile_width: float,
    tile_height: float
) -> Tuple[float, float]:
    """Convert Run8 local coordinates to world tile-based coordinates.

    Args:
        x: Local X coordinate within the tile (meters)
        z: Local Z coordinate within the tile (meters)
        tile_index: (tile_x, tile_z) grid coordinates of the tile
        home_tile: (home_x, home_z) reference tile for origin
        tile_width: Width of each tile in meters
        tile_height: Height of each tile in meters

    Returns:
        (world_x, world_y) in meters relative to home_tile origin.
    """
    tile_x, tile_z = tile_index
    home_x, home_z = home_tile

    world_x = (tile_x - home_x) * tile_width + x
    world_y = (tile_z - home_z) * tile_height - z

    return (world_x, world_y)


def interpolate_curve(start_pos, end_pos, radius, arc_length, curve_sign, num_segments=10,
                      curve_degrees=None):
    """Interpolate points along a curved track segment in local coordinates"""
    x1, y1, z1 = start_pos
    x2, y2, z2 = end_pos

    # If radius is too small, return straight line
    if abs(radius) < 0.1:
        return [(x1, z1), (x2, z2)]

    # Calculate chord vector (in x-z plane)
    dx = x2 - x1
    dz = z2 - z1
    chord_len = math.sqrt(dx*dx + dz*dz)

    # If chord is too small, return just the endpoints
    if chord_len < 0.1:
        return [(x1, z1), (x2, z2)]

    # Midpoint of chord
    mid_x = (x1 + x2) / 2.0
    mid_z = (z1 + z2) / 2.0

    # For semicircles (curve_degrees ≈ 180), the standard formula h² = r² - (chord/2)²
    # gives h ≈ 0, which places the center on the chord and breaks the arc generation.
    # Use a parametric approach instead for semicircles.
    is_semicircle = curve_degrees is not None and abs(abs(curve_degrees) - 180) < 5.0

    if is_semicircle:
        # For a semicircle, the center is at the midpoint of the chord.
        # Generate points parametrically by sweeping 180 degrees around the center.
        # The perpendicular direction determines which side the arc bulges toward.

        # Unit vector along chord (from start to end)
        chord_unit_x = dx / chord_len
        chord_unit_z = dz / chord_len

        # Perpendicular unit vector (rotated 90 degrees CCW)
        perp_x = -chord_unit_z
        perp_z = chord_unit_x

        # The center is at the midpoint
        center_x = mid_x
        center_z = mid_z

        # Generate points by sweeping from start angle to end angle (180 degrees)
        # curve_sign determines which direction the arc bulges
        points = [(x1, z1)]

        for i in range(1, num_segments):
            t = i / num_segments
            # Angle from 0 to pi (180 degrees)
            theta = t * math.pi

            # Parametric semicircle: start at one end, sweep to the other
            # Position along chord: goes from -radius to +radius (relative to center)
            chord_offset = -math.cos(theta) * radius  # -r at t=0, +r at t=1
            # Perpendicular offset: bulges out by sin(theta) * radius
            # Negate curve_sign to match the coordinate system convention
            perp_offset = math.sin(theta) * radius * (-curve_sign)

            x = center_x + chord_offset * chord_unit_x + perp_offset * perp_x
            z = center_z + chord_offset * chord_unit_z + perp_offset * perp_z

            points.append((x, z))

        points.append((x2, z2))
        return points

    # Standard arc calculation for non-semicircle curves
    # Perpendicular to chord (rotated 90 degrees)
    perp_x = -dz / chord_len
    perp_z = dx / chord_len

    # Calculate center of circular arc
    h_squared = radius*radius - (chord_len/2.0)**2
    if h_squared < 0:
        return [(x1, z1), (x2, z2)]
    h = math.sqrt(h_squared)

    center_x = mid_x + h * perp_x * curve_sign
    center_z = mid_z + h * perp_z * curve_sign

    # Calculate angles for start and end points relative to center
    angle_start = math.atan2(z1 - center_z, x1 - center_x)
    angle_end = math.atan2(z2 - center_z, x2 - center_x)

    # Calculate angle sweep
    angle_diff = angle_end - angle_start

    # Check if we should use arc_length to determine sweep direction
    if arc_length > 0 and abs(radius) > 0:
        expected_angle = arc_length / abs(radius)

        # Normalize angle_diff to a reasonable range first
        while angle_diff > math.pi:
            angle_diff -= 2 * math.pi
        while angle_diff < -math.pi:
            angle_diff += 2 * math.pi

        # If arc indicates long sweep (>180 deg), adjust angle_diff accordingly
        if expected_angle > math.pi:
            # We need a long arc. Check if current angle_diff is going the right direction
            # but is the short path
            if abs(angle_diff) < math.pi:
                # Short path detected, extend to long path in the appropriate direction
                if angle_diff > 0:
                    # Extend positive direction: keep going positive past 180
                    angle_diff = angle_diff - 2 * math.pi  # This makes it large and negative (long way)
                else:
                    # Extend negative direction: keep going negative past -180
                    angle_diff = angle_diff + 2 * math.pi  # This makes it large and positive (long way)
    else:
        # No arc_length info, use standard normalization
        while angle_diff > math.pi:
            angle_diff -= 2 * math.pi
        while angle_diff < -math.pi:
            angle_diff += 2 * math.pi

    # Generate intermediate points
    points = [(x1, z1)]

    for i in range(1, num_segments):
        t = i / num_segments
        angle = angle_start + angle_diff * t

        x = center_x + radius * math.cos(angle)
        z = center_z + radius * math.sin(angle)

        points.append((x, z))

    points.append((x2, z2))

    return points


def interpolate_curve_geographic(start_latlon, end_latlon, radius, arc_length, curve_sign,
                                 curve_degrees, num_segments=10):
    """Interpolate a curved track segment between two geographic points"""
    start_lat, start_lon = start_latlon
    end_lat, end_lon = end_latlon

    origin_lat = start_lat
    origin_lon = start_lon

    # Flip curve sign for angles > 180
    if abs(curve_degrees) > 180:
        curve_sign = -curve_sign

    def latlon_to_local_meters(lat, lon):
        meters_north = (lat - origin_lat) * METERS_PER_DEGREE_LAT
        meters_per_degree_lon = METERS_PER_DEGREE_LAT * math.cos(math.radians(origin_lat))
        meters_east = (lon - origin_lon) * meters_per_degree_lon
        return (meters_east, meters_north)

    def local_meters_to_latlon(x, z):
        lat = origin_lat + z / METERS_PER_DEGREE_LAT
        meters_per_degree_lon = METERS_PER_DEGREE_LAT * math.cos(math.radians(origin_lat))
        lon = origin_lon + x / meters_per_degree_lon
        return (lat, lon)

    start_local = latlon_to_local_meters(start_lat, start_lon)
    end_local = latlon_to_local_meters(end_lat, end_lon)

    curve_points_local = interpolate_curve(
        (start_local[0], 0, start_local[1]),
        (end_local[0], 0, end_local[1]),
        radius,
        arc_length,
        -curve_sign,  # Negate for positive-north z-axis
        num_segments,
        curve_degrees
    )

    curve_points_latlon = []
    for x, z in curve_points_local:
        lat, lon = local_meters_to_latlon(x, z)
        curve_points_latlon.append((lat, lon))

    return curve_points_latlon


def calculate_section_length(section: TrackSection) -> float:
    """Calculate the length of a track section in meters"""
    total_length = 0.0

    for node in section.nodes:
        if node.is_reverse_path:
            continue
        total_length += node.arcLen_meters

    return total_length


def is_switch(section: TrackSection) -> bool:
    """Check if a section is a switch (has multiple unique paths)

    A true switch/turnout has at least 2 unique diverging paths,
    not just a specific node count.
    """
    if len(section.nodes) <= 2:
        return False

    # Count unique non-reverse paths
    unique_paths = set()
    for node in section.nodes:
        if node.is_reverse_path:
            continue
        start = (round(node.position[0], 1), round(node.position[2], 1))
        end = (round(node.end_position[0], 1), round(node.end_position[2], 1))
        path_sig = tuple(sorted([start, end]))
        unique_paths.add(path_sig)

    return len(unique_paths) >= 2


def select_nodes_to_plot(section: TrackSection) -> List[TrackNode]:
    """Select which nodes to plot based on section type and num_segments

    For non-switch sections with 2 nodes, only plot the node with num_segments > 0.
    This avoids plotting duplicate overlapping geometry.
    """
    # Filter out reverse paths first
    forward_nodes = [n for n in section.nodes if not n.is_reverse_path]

    # Switch sections - plot all forward nodes
    if is_switch(section):
        return forward_nodes

    # Single forward node - plot it
    if len(forward_nodes) == 1:
        return forward_nodes

    # Two forward nodes - apply num_segments logic
    if len(forward_nodes) == 2:
        node0_segments = forward_nodes[0].num_segments
        node1_segments = forward_nodes[1].num_segments

        # If both have 0 or both have >0, plot all (edge case)
        if (node0_segments == 0 and node1_segments == 0) or \
           (node0_segments > 0 and node1_segments > 0):
            return forward_nodes

        # Plot only the node with num_segments > 0
        if node0_segments > 0:
            return [forward_nodes[0]]
        else:
            return [forward_nodes[1]]

    # 3+ forward nodes or 0 nodes - plot all
    return forward_nodes


def find_end_tile(node: TrackNode, section: TrackSection,
                  end_position: Tuple[float, float, float] = None) -> Tuple[int, int]:
    """Find the tile that contains the end position of a node

    For cross-tile sections, the end position may be on a different tile.
    We find this by matching the end position with another node's start position.

    Args:
        node: The node to find the end tile for
        section: The track section containing the node
        end_position: Optional override for the end position to match (uses node.end_position if None)
    """
    end_tile = node.tile_index  # Default to same tile

    # Use provided end_position or fall back to node's end_position
    end_pos = end_position if end_position is not None else node.end_position

    for other_node in section.nodes:
        if other_node is node:
            continue
        # Check if other node's start position matches this node's end position
        if (abs(other_node.position[0] - end_pos[0]) < 0.5 and
            abs(other_node.position[1] - end_pos[1]) < 0.5 and
            abs(other_node.position[2] - end_pos[2]) < 0.5):
            end_tile = other_node.tile_index
            break

    return end_tile


# ---------------------------------------------------------------------------
# Cross-tile switch-leg reconstruction (tangent-based) -- see below.
#
# A switch's short curved (diverging) leg often ends in the *neighbouring* tile.
# That far endpoint is stored in the neighbour tile's local frame and stitched
# back with a single global tile height; right on a block-boundary seam the
# stitch can be a few metres off. Because a diverging leg only travels a few
# metres itself, that error can flip it so it appears to head the wrong way
# (see the SanBernardino 10034 case). The stored per-node tangent bearings are
# self-consistent within one tile, so we rebuild the leg from the throat (a
# reliable in-tile anchor) using those bearings, and -- per "option B" -- also
# snap the connecting neighbour's shared endpoint to the rebuilt far end so the
# joint stays closed. On mid-tile switches this reproduces the stored geometry
# to < 0.01 m, so it is a no-op there and only corrects genuine cross-tile legs.
# ---------------------------------------------------------------------------

def _norm180(angle: float) -> float:
    """Fold an angle (degrees) into (-180, 180]."""
    while angle > 180.0:
        angle -= 360.0
    while angle <= -180.0:
        angle += 360.0
    return angle


def _bearing_dxdz(bearing_deg: float) -> Tuple[float, float]:
    """Compass bearing (north = -z, east = +x, clockwise) -> unit (dx, dz)."""
    r = math.radians(bearing_deg)
    return math.sin(r), -math.cos(r)


def _reconstruct_arc(anchor_xz: Tuple[float, float], start_bearing: float,
                     sweep_deg: float, arc_len: float, n: int) -> List[Tuple[float, float]]:
    """Forward-integrate a circular arc from an anchor, returning local (x, z) points.

    The arc starts at ``anchor_xz`` heading ``start_bearing`` and turns a total of
    ``sweep_deg`` (signed) over ``arc_len`` metres. Points are in the anchor's
    tile-local frame.
    """
    n = max(8, int(n))
    x, z = anchor_xz
    heading = start_bearing + (sweep_deg / n) / 2.0
    ds = arc_len / n
    dtheta = sweep_deg / n
    pts = [(x, z)]
    for _ in range(n):
        dx, dz = _bearing_dxdz(heading)
        x += ds * dx
        z += ds * dz
        pts.append((x, z))
        heading += dtheta
    return pts


def _pt2(pos) -> Tuple[float, float]:
    """(x, y, z) local position -> (x, z)."""
    return (pos[0], pos[2])


def _d2(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


def compute_switch_leg_fixes(db: TrackDatabase) -> Tuple[Dict, Dict]:
    """Rebuild cross-tile switch legs from throat anchor + tangent bearings.

    Returns:
        leg_recon: {id(node): (throat_tile, [(x, z), ...], arc_len_m)}
            rebuilt leg points in the throat tile's local frame, for the caller
            to convert with whichever coordinate mode is active.
        point_fix: [(far_tile, far_x, far_z, new_tile, new_x, new_z), ...]
            for each rebuilt leg, the shared far endpoint (original local coords)
            and its corrected local coords; the caller converts both to world and
            snaps any neighbour endpoint that lands on the original so the joint
            closes. Coords are kept unrounded so the world match is exact.
    """
    leg_recon: Dict[int, Tuple] = {}
    point_fix: List[Tuple] = []

    for section in db.sections:
        if not is_switch(section):
            continue

        switch_nodes = [n for n in section.nodes if n.is_switch_node]
        if not switch_nodes:
            continue
        switch_point = _pt2(switch_nodes[0].position)

        # Every drawn (forward) leg that emanates from the throat and crosses a
        # tile boundary -- straight *and* curved (both can hit the seam error).
        for node in section.nodes:
            if node.is_reverse_path:
                continue

            p2, e2 = _pt2(node.position), _pt2(node.end_position)
            d_p, d_e = _d2(p2, switch_point), _d2(e2, switch_point)
            if min(d_p, d_e) > 1.0:
                continue  # neither end is the switch point -> not a throat leg

            if d_p <= d_e:
                throat_xz, throat_tile = p2, node.tile_index
                far_xz, far_tile = e2, find_end_tile(node, section, node.end_position)
                node_pos_is_throat = True
            else:
                throat_xz, throat_tile = e2, find_end_tile(node, section, node.end_position)
                far_xz, far_tile = p2, node.tile_index
                node_pos_is_throat = False

            # Only cross-tile legs are affected by the seam-stitch error.
            if throat_tile == far_tile:
                continue

            is_curved = node.radius_meters > 1.0

            # throat->far bearing at the throat (this node's tangent points
            # position -> end_position; reverse it if position is the far end).
            throat_bearing = (node.tan_deg[1] if node_pos_is_throat
                              else _norm180(node.tan_deg[1] + 180.0))

            if is_curved:
                # A curved leg needs the far-end bearing too, to get the signed
                # sweep. Find the companion node covering the same leg (its
                # forward/reverse twin) and read a bearing at each end.
                comp = None
                for m in section.nodes:
                    if m is node:
                        continue
                    if (abs(m.radius_meters - node.radius_meters) < 0.5 and
                            abs(m.arcLen_meters - node.arcLen_meters) < 0.5):
                        mp, me = _pt2(m.position), _pt2(m.end_position)
                        if (_d2(mp, throat_xz) < 1.0 and _d2(me, far_xz) < 1.0) or \
                           (_d2(mp, far_xz) < 1.0 and _d2(me, throat_xz) < 1.0):
                            comp = m
                            break
                if comp is None:
                    continue
                tb = fb = None
                for N in (node, comp):
                    npos = _pt2(N.position)
                    if _d2(npos, throat_xz) <= _d2(npos, far_xz):
                        tb = N.tan_deg[1]
                    else:
                        fb = _norm180(N.tan_deg[1] + 180.0)
                if tb is None or fb is None:
                    continue
                throat_bearing = tb
                sweep = _norm180(fb - tb)
                # Sanity: the swept angle must match the stored curve degrees.
                if abs(abs(sweep) - abs(node.curve_deg)) > 3.0:
                    continue
            else:
                sweep = 0.0

            n_pts = max(24, int(node.num_segments) * 3) if is_curved else 1
            recon = _reconstruct_arc(throat_xz, throat_bearing, sweep,
                                     node.arcLen_meters, n_pts)

            leg_recon[id(node)] = (throat_tile, recon, node.arcLen_meters)
            far_end = recon[-1]
            point_fix.append((far_tile, far_xz[0], far_xz[1],
                              throat_tile, far_end[0], far_end[1]))

    return leg_recon, point_fix


def extract_sections(db: TrackDatabase,
                     tile_geo_bounds: Dict[Tuple[int, int], Tuple[float, float, float, float]] = None,
                     tile_based_config: Optional[TileBasedConfig] = None) -> List[SectionData]:
    """Extract all sections from a track database

    Each section may have multiple paths (especially switches/turnouts).
    Each path is stored as a separate list of coordinates.

    Args:
        db: Track database to extract from
        tile_geo_bounds: Geographic bounds for tiles (required for geographic mode)
        tile_based_config: Configuration for tile-based coordinates (if provided, uses tile coords)
    """
    sections = []
    use_tile_coords = tile_based_config is not None

    # Tangent-based rebuild of cross-tile switch legs (+ neighbour endpoint snap).
    leg_recon, point_fix = compute_switch_leg_fixes(db)

    def _to_world(tile, x, z):
        """Local (tile, x, z) -> world/lat-lon in the active coord mode (or None)."""
        if use_tile_coords:
            return convert_run8_to_tile_coords(
                x, z, tile, tile_based_config.home_tile,
                tile_based_config.tile_width, tile_based_config.tile_height)
        if tile not in tile_geo_bounds:
            return None
        return convert_run8_to_latlon(x, z, tile_geo_bounds[tile])

    def _leg_to_path(throat_tile, local_pts):
        """Convert a rebuilt leg's local (x, z) points to the active coord mode."""
        out = [_to_world(throat_tile, px, pz) for (px, pz) in local_pts]
        return None if any(p is None for p in out) else out

    # World-space snap map: original shared far endpoint -> corrected location.
    # Applied (tapered) to any neighbour path that ends/starts on that point so
    # the joint closes without disturbing the neighbour's opposite end.
    def _wround(pt):
        return (round(pt[0], 2), round(pt[1], 2)) if use_tile_coords \
            else (round(pt[0], 6), round(pt[1], 6))

    point_fix_world = {}
    for (kt, kx, kz, vt, vx, vz) in point_fix:
        orig_w = _to_world(kt, kx, kz)
        corr_w = _to_world(vt, vx, vz)
        if orig_w is not None and corr_w is not None:
            point_fix_world[_wround(orig_w)] = corr_w

    def _snap_path_endpoints(path):
        """Taper a path so a matching end lands on its corrected location."""
        n = len(path)
        if n < 2:
            return
        corr = point_fix_world.get(_wround(path[-1]))
        if corr is not None:
            dx, dy = corr[0] - path[-1][0], corr[1] - path[-1][1]
            for i in range(n):
                f = i / (n - 1)
                path[i] = (path[i][0] + dx * f, path[i][1] + dy * f)
        corr = point_fix_world.get(_wround(path[0]))
        if corr is not None:
            dx, dy = corr[0] - path[0][0], corr[1] - path[0][1]
            for i in range(n):
                f = 1.0 - i / (n - 1)
                path[i] = (path[i][0] + dx * f, path[i][1] + dy * f)

    for section in db.sections:
        # Select which nodes to plot
        nodes_to_plot = select_nodes_to_plot(section)

        # For 2-node sections with num_segments pattern (1, 0), find partner positions
        # When one node has num_segments=1 and the other has num_segments=0,
        # use the 0-segment node's position as the end point (the 1-segment node's
        # end_position may contain garbage data)
        forward_nodes = [n for n in section.nodes if not n.is_reverse_path]
        partner_end_position = {}  # node -> position to use as end point

        if len(forward_nodes) == 2 and not is_switch(section):
            n0_seg = forward_nodes[0].num_segments
            n1_seg = forward_nodes[1].num_segments

            # One has num_segments=1, one has 0 - use the 0-segment node's position as end
            if n0_seg == 1 and n1_seg == 0:
                partner_end_position[forward_nodes[0]] = forward_nodes[1].position
            elif n1_seg == 1 and n0_seg == 0:
                partner_end_position[forward_nodes[1]] = forward_nodes[0].position

        all_paths = []  # List of paths, one per node
        total_length_m = 0.0
        elevation_start_m = 0.0
        elevation_end_m = 0.0
        first_node_processed = False

        for node in nodes_to_plot:
            start_tile = node.tile_index

            # Determine end position - use partner's position if available (num_segments=1 case)
            if node in partner_end_position:
                effective_end_position = partner_end_position[node]
            else:
                effective_end_position = node.end_position

            # Track start/end elevation (Y axis) for gradient calculation
            if not first_node_processed:
                elevation_start_m = node.position[1]
                first_node_processed = True
            elevation_end_m = effective_end_position[1]

            # Cross-tile switch leg: draw the tangent-rebuilt geometry instead of
            # the seam-stitched endpoint (fixes "backward" diverging legs at tile
            # edges). Neighbour joints are closed by _snap_path_endpoints below.
            if id(node) in leg_recon:
                throat_tile, local_pts, arc_len = leg_recon[id(node)]
                path_points = _leg_to_path(throat_tile, local_pts)
                if path_points and len(path_points) >= 2:
                    total_length_m += arc_len
                    all_paths.append(path_points)
                continue

            if use_tile_coords:
                # Tile-based coordinate mode
                # Find end tile for cross-tile sections
                end_tile = find_end_tile(node, section, effective_end_position)

                # Convert to world tile coordinates
                start_x, start_y = convert_run8_to_tile_coords(
                    node.position[0], node.position[2],
                    start_tile,
                    tile_based_config.home_tile,
                    tile_based_config.tile_width,
                    tile_based_config.tile_height
                )
                end_x, end_y = convert_run8_to_tile_coords(
                    effective_end_position[0], effective_end_position[2],
                    end_tile,
                    tile_based_config.home_tile,
                    tile_based_config.tile_width,
                    tile_based_config.tile_height
                )

                # Check if curved
                is_curved = abs(node.radius_meters) > 0.1

                if is_curved:
                    # For cross-tile curves, transform end position to start tile's
                    # extended coordinate space before interpolating
                    end_pos_x = effective_end_position[0]
                    end_pos_y = effective_end_position[1]
                    end_pos_z = effective_end_position[2]

                    if end_tile != start_tile:
                        # Adjust end position to be in start tile's extended coordinates
                        tile_diff_x = end_tile[0] - start_tile[0]
                        tile_diff_z = end_tile[1] - start_tile[1]
                        end_pos_x += tile_diff_x * tile_based_config.tile_width
                        # Z increases going North, but local z is negative going North
                        end_pos_z -= tile_diff_z * tile_based_config.tile_height

                    # Determine curve sign for interpolation
                    # Flip for curves > 180 degrees (same as geographic mode)
                    curve_sign = node.curve_sign
                    if abs(node.curve_deg) > 180:
                        curve_sign = -curve_sign

                    # Interpolate curve in local coordinates directly
                    curve_points_local = interpolate_curve(
                        (node.position[0], node.position[1], node.position[2]),
                        (end_pos_x, end_pos_y, end_pos_z),
                        abs(node.radius_meters),
                        node.arcLen_meters,
                        curve_sign,
                        max(50, int(node.num_segments / 4)),
                        node.curve_deg
                    )
                    # Convert each point to world tile coordinates
                    # For cross-tile curves, determine which tile each point belongs to
                    path_points = []
                    start_tile_x, start_tile_z = start_tile
                    for local_x, local_z in curve_points_local:
                        # Calculate tile offset from start tile based on local coordinates
                        # X increases going East, each tile is tile_width meters
                        tile_offset_x = int(local_x // tile_based_config.tile_width)
                        # Z is negative going North, need to handle correctly
                        # local_z ranges from 0 to -tile_height within a tile
                        if local_z >= 0:
                            tile_offset_z = 0
                        else:
                            # Negative z means we might be in a northern tile
                            tile_offset_z = int((-local_z) // tile_based_config.tile_height)

                        # Adjust local coordinates to be within tile bounds
                        adjusted_x = local_x - (tile_offset_x * tile_based_config.tile_width)
                        adjusted_z = local_z + (tile_offset_z * tile_based_config.tile_height)

                        # Calculate actual tile for this point
                        point_tile = (start_tile_x + tile_offset_x, start_tile_z + tile_offset_z)

                        world_x, world_y = convert_run8_to_tile_coords(
                            adjusted_x, adjusted_z,
                            point_tile,
                            tile_based_config.home_tile,
                            tile_based_config.tile_width,
                            tile_based_config.tile_height
                        )
                        path_points.append((world_x, world_y))
                    total_length_m += node.arcLen_meters
                else:
                    # Straight segment - compute distance from world coordinates,
                    # not raw local tile positions (which break for cross-tile sections)
                    path_points = [(start_x, start_y), (end_x, end_y)]
                    dx = end_x - start_x
                    dy = end_y - start_y
                    total_length_m += math.sqrt(dx*dx + dy*dy)

                if len(path_points) >= 2:
                    # Filter out zero-length paths (in meters now)
                    seg_length = math.sqrt(
                        (path_points[-1][0] - path_points[0][0])**2 +
                        (path_points[-1][1] - path_points[0][1])**2
                    )
                    if seg_length > 0.1:  # 0.1 meters threshold
                        all_paths.append(path_points)

            else:
                # Geographic coordinate mode (original behavior)
                if start_tile not in tile_geo_bounds:
                    continue

                # Find end tile (may be different for cross-tile sections)
                end_tile = find_end_tile(node, section, effective_end_position)
                if end_tile not in tile_geo_bounds:
                    end_tile = start_tile  # Fall back to start tile
                    if end_tile not in tile_geo_bounds:
                        continue

                # Convert start/end to lat/lon using correct tiles
                start_lat, start_lon = convert_run8_to_latlon(
                    node.position[0], node.position[2], tile_geo_bounds[start_tile]
                )
                end_lat, end_lon = convert_run8_to_latlon(
                    effective_end_position[0], effective_end_position[2], tile_geo_bounds[end_tile]
                )

                # Check if curved
                is_curved = abs(node.radius_meters) > 0.1

                if is_curved:
                    # Interpolate curve in geographic space
                    path_points = interpolate_curve_geographic(
                        (start_lat, start_lon),
                        (end_lat, end_lon),
                        abs(node.radius_meters),
                        node.arcLen_meters,
                        node.curve_sign,
                        node.curve_deg,
                        num_segments=max(50, int(node.num_segments / 4))
                    )
                    total_length_m += node.arcLen_meters
                else:
                    # Straight segment - compute distance from lat/lon endpoints,
                    # not raw local tile positions (which break for cross-tile sections)
                    path_points = [(start_lat, start_lon), (end_lat, end_lon)]
                    center_lat = (start_lat + end_lat) / 2
                    dlat_m = (end_lat - start_lat) * METERS_PER_DEGREE_LAT
                    dlon_m = (end_lon - start_lon) * METERS_PER_DEGREE_LAT * math.cos(math.radians(center_lat))
                    total_length_m += math.sqrt(dlat_m*dlat_m + dlon_m*dlon_m)

                if len(path_points) >= 2:
                    # Filter out zero-length or near-zero-length paths (renders as circles)
                    seg_length = math.sqrt(
                        (path_points[-1][0] - path_points[0][0])**2 +
                        (path_points[-1][1] - path_points[0][1])**2
                    )
                    # Only add if segment length is meaningful (> ~1 meter in degrees)
                    if seg_length > 0.00001:
                        all_paths.append(path_points)

        # Close joints: snap any endpoint that lands on a rebuilt switch far point.
        if point_fix_world:
            for path in all_paths:
                _snap_path_endpoints(path)

        if all_paths:
            length_ft = total_length_m * 3.28084

            sections.append(SectionData(
                id=section.index,
                paths=all_paths,
                length_ft=round(length_ft, 1),
                length_m=round(total_length_m, 1),
                is_switch=is_switch(section),
                track_type=section.track_type,
                retarder_mph=section.retarder_mph,
                elevation_start_m=round(elevation_start_m, 2),
                elevation_end_m=round(elevation_end_m, 2)
            ))

    return sections


def extract_signals(signal_db: SignalDatabase,
                    tile_geo_bounds: Dict[Tuple[int, int], Tuple[float, float, float, float]] = None,
                    tile_based_config: Optional[TileBasedConfig] = None) -> List[SignalData]:
    """Extract all signals from a signal database

    Args:
        signal_db: Signal database to extract from
        tile_geo_bounds: Geographic bounds for tiles (required for geographic mode)
        tile_based_config: Configuration for tile-based coordinates (if provided, uses tile coords)
    """
    signals = []
    use_tile_coords = tile_based_config is not None

    for signal in signal_db.signal_heads:
        tile = signal.tile_xz

        if use_tile_coords:
            # Tile-based coordinate mode
            x, y = convert_run8_to_tile_coords(
                signal.position[0], signal.position[2],
                tile,
                tile_based_config.home_tile,
                tile_based_config.tile_width,
                tile_based_config.tile_height
            )
            lat, lon = x, y  # Store as lat/lon fields but actually tile coords
        else:
            # Geographic coordinate mode
            if tile not in tile_geo_bounds:
                continue
            bounds = tile_geo_bounds[tile]
            lat, lon = convert_run8_to_latlon(signal.position[0], signal.position[2], bounds)

        # Get stacked signal IDs (for multi-head signals)
        stacked_ids = signal.signal_indices if signal.signal_indices else [signal.signal_index]

        signals.append(SignalData(
            id=signal.signal_index,
            lat=lat,
            lon=lon,
            rotation=signal.rotation_degrees_y,
            signal_type="absolute" if signal.is_absolute else "intermediate",
            name=f"Signal {signal.signal_index}",
            model_name=signal.model_name,
            is_dwarf=signal.is_dwarf,
            is_switch_indicator=signal.is_switch_indicator,
            is_advance_diverging=signal.is_advance_diverging,
            stacked_ids=stacked_ids
        ))

    return signals


def extract_ai_locations(ai_db_path: str,
                         route_prefix: int,
                         section_map: Dict[int, TrackSection],
                         tile_geo_bounds: Dict[Tuple[int, int], Tuple[float, float, float, float]] = None,
                         tile_based_config: Optional[TileBasedConfig] = None) -> List[AILocationData]:
    """Extract AI locations for a specific route prefix

    Args:
        ai_db_path: Path to AI locations database file
        route_prefix: Route prefix to filter by
        section_map: Map of section IDs to TrackSection objects
        tile_geo_bounds: Geographic bounds for tiles (required for geographic mode)
        tile_based_config: Configuration for tile-based coordinates (if provided, uses tile coords)
    """
    locations = []
    use_tile_coords = tile_based_config is not None

    # Parse the AI locations file
    with open(ai_db_path, 'rb') as f:
        mem_map = f.read()

    spawn_file = SpawnFile()
    ptr = 0
    spawn_file.unk1 = mem_map[ptr:ptr + 4]
    ptr += 4
    spawn_file.num_rec = int.from_bytes(mem_map[ptr:ptr + 4], 'little')
    ptr += 4

    for i in range(spawn_file.num_rec):
        spawn = SpawnPoint(mem_map, ptr)
        spawn_file.spawn_points.append(spawn)
        ptr += len(spawn)

    # Filter by route prefix and calculate positions
    for idx, spawn in enumerate(spawn_file.spawn_points):
        if spawn.route_prefix != route_prefix:
            continue

        track_id = spawn.track_id
        if track_id not in section_map:
            continue

        section = section_map[track_id]
        if not section.nodes:
            continue

        # Get position from first node
        node = section.nodes[0]
        tile = node.tile_index

        if not use_tile_coords:
            if tile not in tile_geo_bounds:
                continue
            bounds = tile_geo_bounds[tile]

        # Calculate position along track
        distance = struct.unpack('<f', spawn.unk4)[0]

        # Simple approximation: use start position plus distance along tangent
        total_length = sum(n.arcLen_meters for n in section.nodes if not n.is_reverse_path)
        if total_length > 0:
            ratio = min(distance / total_length, 1.0) if total_length > 0 else 0
        else:
            ratio = 0

        # Interpolate between start and end of section
        if len(section.nodes) >= 1:
            start_node = section.nodes[0]
            end_node = section.nodes[-1] if not section.nodes[-1].is_reverse_path else section.nodes[0]

            # Determine effective end position for interpolation
            # For 2-node sections with num_segments pattern (1,0), use the 0-segment node's position
            forward_nodes = [n for n in section.nodes if not n.is_reverse_path]
            if len(forward_nodes) == 2:
                n0_seg = forward_nodes[0].num_segments
                n1_seg = forward_nodes[1].num_segments
                if n0_seg == 1 and n1_seg == 0:
                    end_pos = forward_nodes[1].position
                elif n1_seg == 1 and n0_seg == 0:
                    end_pos = forward_nodes[0].position
                else:
                    end_pos = end_node.end_position
            else:
                end_pos = end_node.end_position

            x = start_node.position[0] + ratio * (end_pos[0] - start_node.position[0])
            z = start_node.position[2] + ratio * (end_pos[2] - start_node.position[2])

            if use_tile_coords:
                lat, lon = convert_run8_to_tile_coords(
                    x, z, tile,
                    tile_based_config.home_tile,
                    tile_based_config.tile_width,
                    tile_based_config.tile_height
                )
            else:
                lat, lon = convert_run8_to_latlon(x, z, bounds)

            locations.append(AILocationData(
                id=idx,
                name=spawn.name,
                lat=lat,
                lon=lon,
                type_id=spawn.type,
                type_name=SPAWN_TYPE_NAMES.get(spawn.type, f"Unknown ({spawn.type})")
            ))

    return locations


def _decode_r8string(mem_map: bytes, offset: int) -> Tuple[str, int]:
    """Decode an R8String (int32 byte-length + 4-bit-rotated UTF-16 payload).

    Returns (decoded_string, next_offset). Mirrors the rotation used by the
    SpawnPoint / Industry parsers in r8lib.
    """
    n = int.from_bytes(mem_map[offset:offset + INTLEN], 'little', signed=True)
    offset += INTLEN
    s = ''.join(chr(mem_map[k] << 4 | mem_map[k + 1] >> 4)
                for k in range(offset, offset + n, 2))
    return s, offset + n


def _milepost_label(milepost: str, station: str) -> str:
    """Build a display label from the two milepost strings.

    Routes use these two fields inconsistently: some carry a numeric mile value
    plus a real place name ("13.6" / "Hodge"), some use "MP" as a placeholder
    station for a plain numbered post, and some leave the mile value empty and
    put the whole label in the station field ("ANA599.6 South Braganza").
    """
    mp = milepost.strip()
    st = station.strip()
    if st and st.upper() != 'MP':
        return f"{st} (MP {mp})" if mp else st
    if mp:
        return f"MP {mp}"
    return st or "Milepost"


def extract_mileposts(milepost_db_path: str,
                      tile_geo_bounds: Dict[Tuple[int, int], Tuple[float, float, float, float]] = None,
                      tile_based_config: Optional[TileBasedConfig] = None) -> List[MilepostData]:
    """Extract mileposts/markers from a MilepostDatabase.r8 file.

    File format (see MilepostDatabase.md):
        header: int32 reserved, int32 count
        record: int32 reserved, R8String milepost, R8String station,
                int32 tile_x, int32 tile_z, float x, float y, float z
    The (x, z) pair is the Run8 local position within the tile (y is elevation).

    Args:
        milepost_db_path: Path to MilepostDatabase.r8
        tile_geo_bounds: Geographic bounds for tiles (required for geographic mode)
        tile_based_config: Configuration for tile-based coordinates (if provided, uses tile coords)
    """
    mileposts = []
    use_tile_coords = tile_based_config is not None

    with open(milepost_db_path, 'rb') as f:
        mem_map = f.read()

    ptr = INTLEN  # skip reserved
    count = int.from_bytes(mem_map[ptr:ptr + INTLEN], 'little', signed=True)
    ptr += INTLEN

    for i in range(count):
        ptr += INTLEN  # per-record reserved
        milepost, ptr = _decode_r8string(mem_map, ptr)
        station, ptr = _decode_r8string(mem_map, ptr)
        tile_x = int.from_bytes(mem_map[ptr:ptr + INTLEN], 'little', signed=True)
        ptr += INTLEN
        tile_z = int.from_bytes(mem_map[ptr:ptr + INTLEN], 'little', signed=True)
        ptr += INTLEN
        x, y, z = struct.unpack('<fff', mem_map[ptr:ptr + FLTLEN * 3])
        ptr += FLTLEN * 3

        tile = (tile_x, tile_z)
        if use_tile_coords:
            lat, lon = convert_run8_to_tile_coords(
                x, z, tile,
                tile_based_config.home_tile,
                tile_based_config.tile_width,
                tile_based_config.tile_height
            )
        else:
            if tile not in tile_geo_bounds:
                continue
            lat, lon = convert_run8_to_latlon(x, z, tile_geo_bounds[tile])

        mileposts.append(MilepostData(
            id=i,
            label=_milepost_label(milepost, station),
            milepost=milepost,
            station=station,
            lat=lat,
            lon=lon,
            elevation_m=round(y, 2)
        ))

    return mileposts


def extract_industries(industry_db_path: str,
                       route_prefix: int,
                       section_map: Dict[int, TrackSection],
                       tile_geo_bounds: Dict[Tuple[int, int], Tuple[float, float, float, float]] = None,
                       tile_based_config: Optional[TileBasedConfig] = None) -> List[IndustryData]:
    """Extract industries for a specific route prefix

    Args:
        industry_db_path: Path to industry database file
        route_prefix: Route prefix to filter by
        section_map: Map of section IDs to TrackSection objects
        tile_geo_bounds: Geographic bounds for tiles (required for geographic mode)
        tile_based_config: Configuration for tile-based coordinates (if provided, uses tile coords)
    """
    industries = []
    use_tile_coords = tile_based_config is not None

    # Parse the industry file
    with open(industry_db_path, 'rb') as f:
        mem_map = f.read()

    industry_file = IndustryFile()
    ptr = 0
    industry_file.unk1 = mem_map[ptr:ptr + 4]
    ptr += 4
    industry_file.num_rec = int.from_bytes(mem_map[ptr:ptr + 4], 'little')
    ptr += 4

    for i in range(industry_file.num_rec):
        industry = Industry(mem_map, ptr)
        industry_file.industries.append(industry)
        ptr += len(industry)

    # Filter by route prefix and calculate positions
    for industry in industry_file.industries:
        if not hasattr(industry, 'track') or industry.number_of_tracks == 0:
            continue

        # Collect all track sections for this industry that match our route prefix
        track_section_ids = []
        first_section_id = None
        for track in industry.track:
            if track.route_prefix == route_prefix:
                track_section_ids.append(track.track_section)
                if first_section_id is None:
                    first_section_id = track.track_section

        if not track_section_ids:
            continue

        # Get position from first track section
        if first_section_id not in section_map:
            continue

        section = section_map[first_section_id]
        if not section.nodes:
            continue

        node = section.nodes[0]
        tile = node.tile_index

        if not use_tile_coords:
            if tile not in tile_geo_bounds:
                continue
            bounds = tile_geo_bounds[tile]

        # Determine effective end position for midpoint calculation
        # For 2-node sections with num_segments pattern (1,0), use the 0-segment node's position
        forward_nodes = [n for n in section.nodes if not n.is_reverse_path]
        if len(forward_nodes) == 2:
            n0_seg = forward_nodes[0].num_segments
            n1_seg = forward_nodes[1].num_segments
            if n0_seg == 1 and n1_seg == 0:
                effective_end = forward_nodes[1].position
                end_tile = forward_nodes[1].tile_index
            elif n1_seg == 1 and n0_seg == 0:
                effective_end = forward_nodes[0].position
                end_tile = forward_nodes[0].tile_index
            else:
                effective_end = node.end_position
                end_tile = find_end_tile(node, section, effective_end)
        else:
            effective_end = node.end_position
            end_tile = find_end_tile(node, section, effective_end)

        # Convert start and end to world coordinates, then calculate midpoint
        # This handles cross-tile sections correctly
        if use_tile_coords:
            start_x, start_y = convert_run8_to_tile_coords(
                node.position[0], node.position[2], tile,
                tile_based_config.home_tile,
                tile_based_config.tile_width,
                tile_based_config.tile_height
            )
            end_x, end_y = convert_run8_to_tile_coords(
                effective_end[0], effective_end[2], end_tile,
                tile_based_config.home_tile,
                tile_based_config.tile_width,
                tile_based_config.tile_height
            )
            # Midpoint in world coordinates
            lat = (start_x + end_x) / 2
            lon = (start_y + end_y) / 2
        else:
            # For geographic mode, also handle cross-tile properly
            if end_tile not in tile_geo_bounds:
                end_tile = tile  # Fall back to start tile
            start_lat, start_lon = convert_run8_to_latlon(
                node.position[0], node.position[2], bounds
            )
            end_bounds = tile_geo_bounds.get(end_tile, bounds)
            end_lat, end_lon = convert_run8_to_latlon(
                effective_end[0], effective_end[2], end_bounds
            )
            # Midpoint in geographic coordinates
            lat = (start_lat + end_lat) / 2
            lon = (start_lon + end_lon) / 2

        industries.append(IndustryData(
            tag=industry.trk_sym,
            name=industry.name,
            local_name=industry.local_name,
            lat=lat,
            lon=lon,
            track_sections=track_section_ids
        ))

    return industries


# ---------------------------------------------------------------------------
# Rail-vehicle placement (world-save import)
#
# Each rail vehicle reports two trucks; each truck gives a Run8 track section,
# a start-node end, and a distance along that section (metres). We place a truck
# by walking that many metres along the section's *already-extracted* polyline
# (SectionData.paths), so vehicles land exactly on the drawn track in whatever
# coordinate mode is active (tile-world metres or lat/lon). The car body is the
# sub-polyline between its two truck points, so it follows curves. Ordering is
# geometry-only: a vehicle's sort key is the yard-direction-most (minimum) of
# its two trucks' distance-from-polyline-start, matching the coordinate model in
# world-import-rail-vehicle-ordering.md without the YARDS yard-track SEQ layer.
# ---------------------------------------------------------------------------

@dataclass
class RailVehicleData:
    """A placed rail vehicle ready for JSON serialization."""
    train_id: int
    unit_number: str
    destination_tag: str
    unit_type: str
    rv_filename: str
    body: List[Tuple[float, float]]      # polyline (>=2 pts) from truck A to truck B
    position_key: float                  # ordering key (min truck dist-from-start)
    resolved: bool                       # at least one truck landed on a known section
    car_type: str = ""                   # INDUSTRY_CONFIG_CAR_TYPE from the DB (for colouring)
    company: str = ""                    # loco reporting mark (INITIAL) from the DB (for loco colouring)
    front0: bool = True                  # loco facing: True if body[0] is the front end (see _loco_front_at_zero)


@dataclass
class TrainData:
    """A train (consist) with its placed rail vehicles."""
    train_id: int
    was_ai: bool
    vehicles: List[RailVehicleData] = field(default_factory=list)


def _concat_section_polyline(sd: SectionData) -> List[Tuple[float, float]]:
    """Build one clean polyline for the section, for placing rail vehicles.

    A section's ``paths`` may hold the same physical track several times (a coarse
    forward path, its coarse mirror, and a detailed interpolated path) and/or
    genuinely distinct switch legs. The map draws all of them, but placement needs
    a single non-repeating route: blindly concatenating every path produces an
    out-and-back / zigzag polyline of 2-3x the real length (a *degenerate*
    start==end polyline when the mirror halves cancel - e.g. section 2283).

    So: take the most-detailed path as the base, then stitch on only paths that
    *continue* it end-to-start (a genuine multi-segment run), skipping duplicates,
    reverses (same endpoints) and branches (a switch's other leg). The result
    follows one route with honest length.
    """
    paths = [p for p in sd.paths if len(p) >= 2]
    if not paths:
        return []

    def same_span(a, b, tol2=9.0):   # same endpoint pair, either direction (~3 m)
        return ((_d2(a[0], b[0]) < tol2 and _d2(a[-1], b[-1]) < tol2) or
                (_d2(a[0], b[-1]) < tol2 and _d2(a[-1], b[0]) < tol2))

    if getattr(sd, 'is_switch', False):
        # A switch section's detailed path bows ~1 m toward the diverging lead (the
        # points/closure curve). A rail vehicle on the *through* route rides the
        # straight stock rail, so place it along the straight chord between the
        # section's canonical endpoints instead of the bowed lead - otherwise cars
        # sitting on a switch tilt onto the diverging leg. The coarse path is that
        # chord (endpoints = the node positions the neighbours connect to).
        coarse = min(paths, key=len)
        return [tuple(coarse[0]), tuple(coarse[-1])]

    # Base = the most detailed path (most vertices); on a tie, the longer arc.
    base = list(max(paths, key=lambda p: (len(p), _cumulative_lengths(p)[-1])))

    # The detailed interpolated path can drift ~1 m at its ends from the section's
    # canonical node positions, while neighbouring sections connect at those node
    # positions - so an un-snapped detailed path kinks at the seam (visible as a
    # rounded lobe under the wide RV stroke). Snap the base's endpoints to a coarse
    # same-span path's endpoints (the node positions) so seams meet cleanly.
    coarse = min((p for p in paths if p is not base and same_span(p, base)),
                 key=len, default=None)
    if coarse is not None:
        if _d2(coarse[0], base[0]) <= _d2(coarse[-1], base[0]):
            base[0], base[-1] = coarse[0], coarse[-1]
        else:
            base[0], base[-1] = coarse[-1], coarse[0]

    for p in paths:
        if p is base or same_span(p, base):
            continue                       # duplicate / reverse of the base route
        if _d2(base[-1], p[0]) < 0.25:     # p continues from the base's end
            base.extend(p[1:])
        elif _d2(base[-1], p[-1]) < 0.25:  # p continues, reversed
            base.extend(reversed(p[:-1]))
        # otherwise p is a branch / disjoint -> ignore for placement
    return base


def _cumulative_lengths(poly: List[Tuple[float, float]]) -> List[float]:
    """Cumulative arc length at each vertex of a polyline (poly[0] -> 0)."""
    cum = [0.0]
    for a, b in zip(poly, poly[1:]):
        cum.append(cum[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
    return cum


def _point_at_distance(poly: List[Tuple[float, float]], cum: List[float],
                       d: float) -> Tuple[float, float]:
    """Interpolate the point at arc length ``d`` from ``poly[0]`` (clamped)."""
    total = cum[-1]
    d = min(max(d, 0.0), total)
    for i in range(1, len(cum)):
        if d <= cum[i] or i == len(cum) - 1:
            seg = cum[i] - cum[i - 1]
            t = 0.0 if seg <= 1e-9 else (d - cum[i - 1]) / seg
            a, b = poly[i - 1], poly[i]
            return (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
    return poly[-1]


def _subpolyline(poly: List[Tuple[float, float]], cum: List[float],
                 d0: float, d1: float) -> List[Tuple[float, float]]:
    """Return the polyline vertices between arc lengths ``d0`` and ``d1``.

    Endpoints are interpolated exactly and the result is ordered from ``d0`` to
    ``d1`` (so the body runs from truck A toward truck B). Used to draw a car
    body that follows the track's curvature between its two trucks.
    """
    lo, hi = (d0, d1) if d0 <= d1 else (d1, d0)
    total = cum[-1]
    lo = min(max(lo, 0.0), total)
    hi = min(max(hi, 0.0), total)
    pts = [_point_at_distance(poly, cum, lo)]
    for i in range(len(poly)):
        if lo < cum[i] < hi:
            pts.append(poly[i])
    pts.append(_point_at_distance(poly, cum, hi))
    if d0 > d1:
        pts.reverse()
    # Drop duplicate consecutive points.
    out = [pts[0]]
    for p in pts[1:]:
        if _d2(p, out[-1]) > 1e-6:
            out.append(p)
    return out if len(out) >= 2 else [pts[0], pts[-1]]


class _SectionPlacer:
    """Caches per-section polyline + cumulative lengths, keyed by
    ``(route_prefix, section_index)``.

    Section indices are per-region and collide across regions (Barstow's 446 is a
    different physical section than Needles' 446), so the key must include the
    route prefix. A placer can span *all* regions (they share one tile-world
    coordinate space), which lets a car that straddles a region boundary place
    each truck against the correct region's section.
    """

    def __init__(self, sd_by_key: Dict[Tuple[int, int], SectionData]):
        self._sd = sd_by_key
        self._cache: Dict[Tuple[int, int], Optional[Tuple[List, List, float]]] = {}

    def _geometry(self, key: Tuple[int, int]):
        if key in self._cache:
            return self._cache[key]
        sd = self._sd.get(key)
        geom = None
        if sd is not None:
            poly = _concat_section_polyline(sd)
            if len(poly) >= 2:
                cum = _cumulative_lengths(poly)
                if cum[-1] > 1e-6:
                    geom = (poly, cum, cum[-1])
        self._cache[key] = geom
        return geom

    def truck(self, truck) -> Optional[Tuple[Tuple[float, float], float, Tuple[int, int]]]:
        """Place a truck by its own ``(route_prefix, section_index)``.

        Returns ``(point, dist_from_start, key)`` where ``dist_from_start`` is
        metres from ``polyline[0]`` oriented by the truck's ``start_node_index``
        (0 -> from start, else -> from far end) and ``key`` is the
        ``(prefix, section_index)`` it resolved to, or ``None`` if unknown.
        """
        key = (truck.route_prefix, truck.section_index)
        geom = self._geometry(key)
        if geom is None:
            return None
        poly, cum, total = geom
        d = total - truck.distance_m if truck.start_node_index else truck.distance_m
        d = min(max(d, 0.0), total)
        return (_point_at_distance(poly, cum, d), d, key)

    def body(self, key: Tuple[int, int], d0: float, d1: float) -> List[Tuple[float, float]]:
        """Sub-polyline between two distances on the same section (follows curves)."""
        geom = self._geometry(key)
        if geom is None:
            return []
        poly, cum, _ = geom
        return _subpolyline(poly, cum, d0, d1)


def build_section_placer(region_sections) -> "_SectionPlacer":
    """Build a placer spanning multiple regions.

    ``region_sections`` is an iterable of ``(route_prefix, List[SectionData])``;
    sections are keyed by ``(route_prefix, section.id)``.
    """
    sd_by_key: Dict[Tuple[int, int], SectionData] = {}
    for prefix, sections in region_sections:
        for s in sections:
            sd_by_key.setdefault((prefix, s.id), s)
    return _SectionPlacer(sd_by_key)


def _extend_segment(pa: Tuple[float, float], pb: Tuple[float, float],
                    length: float) -> List[Tuple[float, float]]:
    """Extend a straight A->B segment symmetrically to at least ``length`` units.

    Used for the (rare) case where a vehicle's two trucks are on different
    sections, so there is no single polyline to walk: the body is drawn straight
    and grown past each truck by the overhang. Coordinate units must match
    ``length`` (metres in the tile-world / align coordinate mode).
    """
    dx, dy = pb[0] - pa[0], pb[1] - pa[1]
    d = math.hypot(dx, dy)
    if not length or d < 1e-9 or length <= d:
        return [pa, pb]
    oh = (length - d) / 2.0
    ux, uy = dx / d, dy / d
    return [(pa[0] - ux * oh, pa[1] - uy * oh),
            (pb[0] + ux * oh, pb[1] + uy * oh)]


# --- Within-consist rigid layout (de-overlap) -------------------------------
# A coupled consist is physically a rigid string of cars, end to end. The sim's
# per-truck metres are noisy (compressed topological track) and, on a broken
# section, outright garbage. So rather than draw each car between its own two
# trucks, we lay the whole consist end to end along its section chain: cars in
# world-save (front->back) order, each at its real DB body length, zero gap,
# anchored at the consist midpoint. See rv_cross_section_ordering_plan.md.

_CHAIN_DEGENERATE_TOL = 1.0   # m: a section whose two endpoints are this close is degenerate


def _seg_len(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _consist_section_order(group) -> List[Tuple[int, int]]:
    """Distinct section keys a consist occupies, in front->back order.

    ``group`` items are ``(vehicle, ta, tb, length_m, car_type, company)`` in
    world-save order, where ``ta``/``tb`` are ``placer.truck()`` results
    ``(point, dist, key)`` or ``None``. Sections are ordered by first appearance
    scanning cars front->back (truck A before truck B).
    """
    order, seen = [], set()
    for item in group:
        for t in (item[1], item[2]):
            if t is not None and t[2] not in seen:
                seen.add(t[2])
                order.append(t[2])
    return order


def _is_degenerate_section(placer, key) -> bool:
    g = placer._geometry(key)
    if g is None:
        return True
    poly = g[0]
    return _seg_len(poly[0], poly[-1]) < _CHAIN_DEGENERATE_TOL


def _build_consist_polyline(placer, ordered_keys):
    """Concatenate a consist's sections into one continuous polyline ``P``.

    Sections are oriented greedily (each section's near endpoint joins the
    running end) and degenerate / missing sections are bridged with a straight
    line so ``P`` stays continuous and monotonic front->back. Returns
    ``(P, P_cum, place)`` where ``place[key] = (off, total, flip)`` locates each
    section in ``P``: a truck at arc length ``d`` from that section's own
    ``polyline[0]`` sits at ``P`` arc length ``off + (total - d if flip else d)``.
    Returns ``None`` if no usable (non-degenerate) section is present.
    """
    geoms = []
    for key in ordered_keys:
        g = placer._geometry(key)
        if g is None:
            geoms.append((key, None, 0.0, True))
            continue
        poly, _cum, total = g
        degen = _seg_len(poly[0], poly[-1]) < _CHAIN_DEGENERATE_TOL
        geoms.append((key, poly, total, degen))

    usable = [i for i, gg in enumerate(geoms) if gg[1] is not None and not gg[3]]
    if not usable:
        return None

    P: List[Tuple[float, float]] = []
    P_cum: List[float] = [0.0]

    def push(pt):
        if P and _seg_len(P[-1], pt) < 1e-9:
            return
        if P:
            P_cum.append(P_cum[-1] + _seg_len(P[-1], pt))
        P.append(pt)

    # First usable section: orient it so its far end points toward the next one.
    first = usable[0]
    flip_first = False
    if len(usable) >= 2:
        s0, e0 = geoms[first][1][0], geoms[first][1][-1]
        p1 = geoms[usable[1]][1]
        s1, e1 = p1[0], p1[-1]
        d_end = min(_seg_len(e0, s1), _seg_len(e0, e1))
        d_start = min(_seg_len(s0, s1), _seg_len(s0, e1))
        flip_first = d_start < d_end

    place: Dict[Tuple[int, int], Tuple[float, float, bool]] = {}
    for gi, (key, poly, total, degen) in enumerate(geoms):
        if poly is None or degen:
            # Degenerate / missing: no vertices to push; record a placement at the
            # current running end (used only for reliability, never for drawing).
            place[key] = (P_cum[-1], total, False)
            continue
        if gi == first:
            flip = flip_first
        else:
            cur = P[-1] if P else poly[0]
            flip = _seg_len(poly[-1], cur) < _seg_len(poly[0], cur)
        oriented = list(reversed(poly)) if flip else poly
        push(oriented[0])       # bridge to this section's start (straight if a gap)
        off = P_cum[-1]
        for pt in oriented[1:]:
            push(pt)
        place[key] = (off, total, flip)

    if len(P) < 2:
        return None
    return P, P_cum, place


_CHAIN_ADJ_TOL = 8.0   # m: two sections whose nearest endpoints are within this are adjacent


def _min_endpoint_gap(poly_a, poly_b) -> float:
    a0, a1, b0, b1 = poly_a[0], poly_a[-1], poly_b[0], poly_b[-1]
    return min(_seg_len(a0, b0), _seg_len(a0, b1), _seg_len(a1, b0), _seg_len(a1, b1))


def _split_runs(placer, order):
    """Split a consist's ordered section keys into contiguous runs of usable,
    endpoint-adjacent sections. A degenerate / missing section, or a non-adjacent
    join, ends the current run (and a degenerate/missing section joins no run)."""
    runs, cur = [], []
    for key in order:
        g = placer._geometry(key)
        if g is None or _seg_len(g[0][0], g[0][-1]) < _CHAIN_DEGENERATE_TOL:
            if cur:
                runs.append(cur)
                cur = []
            continue
        if cur and _min_endpoint_gap(placer._geometry(cur[-1])[0], g[0]) > _CHAIN_ADJ_TOL:
            runs.append(cur)
            cur = []
        cur.append(key)
    if cur:
        runs.append(cur)
    return runs


def _layout_consist(group, placer):
    """Lay a consist's cars end to end along its section chain.

    ``group`` is ``[(vehicle, ta, tb, body_len_m, full_len_m, coupler_m, car_type,
    company), ...]`` in world-save (front->back) order. Cars are laid out by their
    full coupled footprint (``full_len_m``, so they abut like a real coupled train
    and the laid length matches the true track span), and each body is drawn inset
    by ``coupler_m`` at each end so the couplers become the visible inter-car gap.
    Returns ``[body | None, ...]`` aligned to
    ``group`` (each body a polyline following the real track; ``None`` for a car
    the caller should place with the per-truck fallback), or ``None`` to fall back
    entirely.

    The consist is split into contiguous **runs** of usable, endpoint-adjacent
    sections (a degenerate / missing / non-adjacent section ends a run). Each run's
    slice of the consist is laid out end to end on that run's own polyline, anchored
    at the median of its cars' true positions. Segmenting keeps a single corrupt
    section (e.g. the degenerate 2283) from stretching the whole train: drift is
    bounded to each run's small gap-compression instead of accumulating across the
    bad joint. Cars on the bad section (in no run) fall back per-truck.
    """
    order = _consist_section_order(group)
    runs = _split_runs(placer, order)
    if not runs:
        return None
    sec_run = {k: ri for ri, run in enumerate(runs) for k in run}

    def car_run(ta, tb):
        # A car belongs to a run only if all of its trucks that land in *some* run
        # land in the SAME one. A car whose two trucks fall in DIFFERENT runs (a
        # boundary straddler, e.g. one truck each side of a yard gap the chain split
        # on) must NOT be force-packed into one run: anchored on a single truck its
        # footprint runs off the end of that run's polyline and _point_at_distance
        # clamps it to a short stub. Returning None drops it to the per-truck
        # fallback, which draws a full-length straight body bridging the two trucks.
        hit = set()
        for t in (ta, tb):
            if t is not None and t[2] in sec_run:
                hit.add(sec_run[t[2]])
        return next(iter(hit)) if len(hit) == 1 else None

    def car_slot(ta, tb, full_len_m, body_len_m):
        # Layout slot = the coupled footprint (cars abut like a real coupled train,
        # so the laid length matches the true span). Fall back to the body length,
        # then the truck span, then a sane default.
        if full_len_m and full_len_m > 0:
            return full_len_m
        if body_len_m and body_len_m > 0:
            return body_len_m
        span = _seg_len(ta[0], tb[0]) if (ta and tb) else 0.0
        return span if 0 < span < 30 else 15.0

    out = [None] * len(group)
    for ri, run in enumerate(runs):
        built = _build_consist_polyline(placer, run)
        if built is None:
            continue
        P, P_cum, place = built
        Ptot = P_cum[-1]

        def truck_P(t):
            if t is None or t[2] not in place:
                return None
            off, total, flip = place[t[2]]
            return off + (total - t[1] if flip else t[1])

        idxs = [i for i in range(len(group)) if car_run(group[i][1], group[i][2]) == ri]
        if not idxs:
            continue

        # Per-car footprint slot + coupler inset + true along-run centre.
        lengths, couplers, centres = {}, {}, {}
        for i in idxs:
            v, ta, tb, body_len_m, full_len_m, coupler_m, ct, co = group[i]
            lengths[i] = car_slot(ta, tb, full_len_m, body_len_m)
            couplers[i] = coupler_m if (coupler_m and coupler_m > 0) else 0.0
            pts = [p for p in (truck_P(ta), truck_P(tb)) if p is not None]
            centres[i] = (sum(pts) / len(pts)) if pts else None

        # Orient the run's laid coordinate to match consist order (P may have been
        # built either way): if reliable centres decrease along the consist, flip.
        rel = [(i, centres[i]) for i in idxs if centres[i] is not None]
        flip_run = len(rel) >= 2 and rel[0][1] > rel[-1][1]

        # Lay cars end to end (zero gap) in consist order; local centre per car.
        local_centre, run_len = {}, 0.0
        for i in idxs:
            local_centre[i] = run_len + lengths[i] / 2.0
            run_len += lengths[i]

        deltas = sorted((Ptot - centres[i] if flip_run else centres[i]) - local_centre[i]
                        for i in idxs if centres[i] is not None)
        if not deltas:
            continue
        m = len(deltas)
        offset = deltas[m // 2] if m % 2 else (deltas[m // 2 - 1] + deltas[m // 2]) / 2.0

        pos = 0.0
        for i in idxs:
            # Slots abut; draw the body inset by one coupler at each end so the
            # dropped couplers become the visible gap between adjacent cars.
            inset = couplers[i] if (2 * couplers[i] < lengths[i]) else 0.0
            lo, hi = offset + pos + inset, offset + pos + lengths[i] - inset
            pos += lengths[i]
            if flip_run:
                lo, hi = Ptot - hi, Ptot - lo
            # A rail car is rigid: draw the body as a STRAIGHT chord between its two
            # end points on the chain (not the sub-polyline), so a car spanning a
            # switch/curve stays a straight rectangle instead of bending onto the
            # diverging leg. Endpoints stay on the track; consecutive cars still abut.
            lo, hi = min(lo, hi), max(lo, hi)
            body = [_point_at_distance(P, P_cum, lo), _point_at_distance(P, P_cum, hi)]
            if _seg_len(body[0], body[1]) > 1e-6:
                out[i] = body
    return out


def _fallback_body(placer, ta, tb, length_m):
    """Per-car body (the pre-de-overlap behaviour): follow the section between the
    two trucks when they share one section, else a straight chord. Returns
    ``(body, key)`` where ``key`` is the ordering key (min truck distance)."""
    if ta and tb and ta[2] == tb[2]:
        lo, hi = (ta[1], tb[1]) if ta[1] <= tb[1] else (tb[1], ta[1])
        if length_m:
            overhang = max(0.0, (length_m - (hi - lo)) / 2.0)
            lo -= overhang
            hi += overhang
        body = placer.body(ta[2], lo, hi)
        key = min(ta[1], tb[1])
    elif ta and tb:
        body = _extend_segment(ta[0], tb[0], length_m)
        key = min(ta[1], tb[1])
    elif ta or tb:
        t = ta or tb
        body = [t[0], t[0]]
        key = t[1]
    else:
        body = []
        key = float("inf")
    return body, key


def _loco_front_at_zero(ta, tb, body) -> bool:
    """Whether the locomotive's FRONT is the ``body[0]`` end (``True``) or ``body[-1]``.

    The facing is taken purely from the RAW placed truck positions: the nose points
    along ``truck B -> truck A`` (calibrated against known consists, e.g. train 710 =
    L R L L R). Crucially this does NOT re-apply the world-save ``reverseDirection``:
    a reversed loco already has its trucks A/B swapped, so the B->A vector flips 180
    on its own. Applying reverseDirection on top double-flips it and makes every loco
    in a consist face the same way (the bug this replaces).

    The body is laid front->back along the consist chain, so ``body[0]`` is a fixed
    side for the whole consist; the front is on that side when the B->A vector points
    from the body midpoint toward ``body[0]``. Falls back to ``body[0]`` (True) when a
    truck is missing, the two trucks coincide, or the body is degenerate.
    """
    if not body or len(body) < 2 or not ta or not tb:
        return True
    ap, bp = ta[0], tb[0]
    fdx, fdy = ap[0] - bp[0], ap[1] - bp[1]     # B -> A (the loco's facing)
    if abs(fdx) + abs(fdy) < 1e-9:
        return True
    mx, my = (body[0][0] + body[-1][0]) / 2.0, (body[0][1] + body[-1][1]) / 2.0
    return ((body[0][0] - mx) * fdx + (body[0][1] - my) * fdy) > 0


def extract_trains(trains, placer: "_SectionPlacer",
                   rv_lengths: Optional[Dict[str, str]] = None,
                   deoverlap: bool = True) -> Dict[int, List[TrainData]]:
    """Place a parsed world save's trains against a multi-region section placer.

    Each truck is located by its own ``(route_prefix, section_index)`` (see
    :class:`_SectionPlacer`), so a car straddling a region boundary places each
    truck against the correct region's section and its body spans the seam. A car
    is assigned to the region of its **truck-A** route prefix.

    Args:
        trains: list of world_parser.Train records.
        placer: a :class:`_SectionPlacer` spanning all regions (see
            :func:`build_section_placer`).
        rv_lengths: optional {rvXMLfilename.lower(): (length_m, coupler_offset_m,
            car_type)} from rv_length_db.load_rv_lengths(). When given, each car
            body is drawn at its real *body* length - the coupled footprint minus
            one coupler per end - so adjacent coupled cars keep a visible gap.
        deoverlap: when True (default), each consist's cars are laid end to end
            along its section chain (front->back order, DB length, midpoint anchor)
            so coupled cars can't overlap. When False, each car is drawn between
            its own two trucks (the raw per-truck placement).

    Returns:
        ``{route_prefix: [TrainData]}`` - cars grouped by their assigned region.
    """
    out: Dict[int, List[TrainData]] = {}

    for train in trains:
        # Resolve every car's trucks + DB length/type/company once, in world-save
        # (front->back) order. Body length = coupled footprint minus a coupler at
        # each end, so adjacent coupled cars keep a visible gap instead of abutting.
        metas = []   # (v, ta, tb, body_len_m, full_len_m, coupler_m, car_type, company)
        for v in train.vehicles:
            body_len_m, full_len_m, coupler_m, car_type, company = None, None, 0.0, "", ""
            if rv_lengths:
                entry = rv_lengths.get((v.rv_filename or '').strip().lower())
                if entry:
                    full_len_m, coupler_m, car_type, company = entry
                    b = full_len_m - 2.0 * coupler_m
                    body_len_m = b if b > 0 else full_len_m
            metas.append((v, placer.truck(v.truck_a), placer.truck(v.truck_b),
                          body_len_m, full_len_m, coupler_m, car_type, company))

        # Group by assigned region (truck-A prefix), preserving consist order. Each
        # region's slice of the consist is laid out independently (a cross-region
        # train is split per region, as before; car bodies still span the seam).
        region_groups: Dict[int, List[Tuple]] = {}
        for meta in metas:
            region_groups.setdefault(meta[0].route_prefix, []).append(meta)

        by_region: Dict[int, List[RailVehicleData]] = {}
        for region_prefix, group in region_groups.items():
            laid = _layout_consist(group, placer) if deoverlap else None
            for i, (v, ta, tb, body_len_m, full_len_m, coupler_m, car_type, company) in enumerate(group):
                resolved = ta is not None or tb is not None
                if laid is not None and laid[i] is not None:
                    body = laid[i]
                    key = min([d for d in ((ta[1] if ta else None),
                                           (tb[1] if tb else None)) if d is not None],
                              default=float("inf"))
                else:
                    body, key = _fallback_body(placer, ta, tb, body_len_m)

                if len(body) < 2:
                    body = body * 2 if body else []
                    if len(body) < 2:
                        resolved = False

                by_region.setdefault(region_prefix, []).append(RailVehicleData(
                    train_id=train.train_id,
                    unit_number=v.unit_number,
                    destination_tag=v.destination_tag,
                    unit_type=v.unit_type,
                    rv_filename=v.rv_filename,
                    body=[(round(p[0], 6), round(p[1], 6)) for p in body],
                    position_key=round(key, 3) if key != float("inf") else -1.0,
                    resolved=resolved,
                    car_type=car_type,
                    company=company,
                    front0=_loco_front_at_zero(ta, tb, body),
                ))

        for prefix, vehicles in by_region.items():
            if vehicles:
                out.setdefault(prefix, []).append(
                    TrainData(train_id=train.train_id, was_ai=train.was_ai,
                              vehicles=vehicles))

    return out


def calculate_bounds(sections: List[SectionData],
                     signals: List[SignalData],
                     ai_locations: List[AILocationData],
                     industries: List[IndustryData],
                     mileposts: List[MilepostData] = None) -> Optional[Tuple[Tuple[float, float], Tuple[float, float]]]:
    """Calculate bounding box from all data points"""
    all_lats = []
    all_lons = []

    for section in sections:
        for path in section.paths:
            for lat, lon in path:
                all_lats.append(lat)
                all_lons.append(lon)

    for signal in signals:
        all_lats.append(signal.lat)
        all_lons.append(signal.lon)

    for loc in ai_locations:
        all_lats.append(loc.lat)
        all_lons.append(loc.lon)

    for ind in industries:
        all_lats.append(ind.lat)
        all_lons.append(ind.lon)

    for mp in (mileposts or []):
        all_lats.append(mp.lat)
        all_lons.append(mp.lon)

    if not all_lats:
        return None

    return ((min(all_lats), min(all_lons)), (max(all_lats), max(all_lons)))


def extract_region(region_config: RegionConfig,
                   industry_db_path: Path,
                   tile_corrections: Dict[Tuple[int, int], Dict[str, float]],
                   default_tile_dir: str,
                   tile_dir: str = None,
                   tile_based_config: Optional[TileBasedConfig] = None) -> RegionData:
    """Extract all data for a single region.

    Trains are NOT placed here: because a car can straddle a region boundary,
    placement runs once against a combined placer over all regions (see
    build_section_placer / extract_trains). The caller attaches RegionData.trains.

    Args:
        region_config: Region configuration
        industry_db_path: Path to the industry database
        tile_corrections: Dict of tile corrections
        default_tile_dir: Default tile directory from config (derived from region_dir)
        tile_dir: Override tile directory (uses region_config.terrain_tile_dir or default_tile_dir if None)
        tile_based_config: Configuration for tile-based coordinates (if provided, uses tile coords)
    """
    print(f"\nExtracting region: {region_config.display_name}")
    use_tile_coords = tile_based_config is not None

    if use_tile_coords:
        print(f"  Using tile-based coordinates (home_tile={tile_based_config.home_tile})")
    else:
        # Determine tile directory: explicit override > region config > default from config
        if tile_dir is None:
            if region_config.terrain_tile_dir:
                tile_dir = str(region_config.terrain_tile_dir)
            else:
                tile_dir = default_tile_dir
        print(f"  Using terrain tiles from: {tile_dir}")

    # Load track database
    print(f"  Loading track database: {region_config.track_database}")
    track_db = TrackDatabase(str(region_config.track_database))
    print(f"  Loaded {len(track_db.sections)} sections")

    # Build section map
    section_map = {sec.index: sec for sec in track_db.sections}

    # Collect all tiles involved
    tiles_involved = set()
    for section in track_db.sections:
        for node in section.nodes:
            tiles_involved.add(node.tile_index)
    print(f"  Found {len(tiles_involved)} unique tiles")

    # Load tile bounds (only needed for geographic mode)
    tile_geo_bounds = {}
    corrected_tiles = set()
    if not use_tile_coords:
        print(f"  Loading tile bounds...")
        tile_geo_bounds, corrected_tiles = load_tile_bounds(tiles_involved, tile_corrections, tile_dir)
        print(f"  Loaded bounds for {len(tile_geo_bounds)} tiles ({len(corrected_tiles)} corrected)")

    # Extract sections
    print(f"  Extracting track sections...")
    sections = extract_sections(track_db, tile_geo_bounds, tile_based_config)
    print(f"  Extracted {len(sections)} sections with coordinates")

    # Load and extract signals (optional - some regions are dark territory)
    signals = []
    if region_config.signal_database.exists():
        print(f"  Loading signal database: {region_config.signal_database}")
        signal_db = SignalDatabase(str(region_config.signal_database))
        signals = extract_signals(signal_db, tile_geo_bounds, tile_based_config)
        print(f"  Extracted {len(signals)} signals")
    else:
        print(f"  No signal database (dark territory) - skipping signals")

    # Extract AI locations (optional - some regions may not have AI locations file)
    ai_locations = []
    if region_config.ai_locations_database.exists():
        print(f"  Extracting AI locations for route prefix {region_config.route_prefix}...")
        ai_locations = extract_ai_locations(
            str(region_config.ai_locations_database),
            region_config.route_prefix,
            section_map,
            tile_geo_bounds,
            tile_based_config
        )
        print(f"  Extracted {len(ai_locations)} AI locations")
    else:
        print(f"  No AI locations database - skipping AI locations")

    # NOTE: milepost extraction is intentionally not wired into the pipeline.
    # The parser (extract_mileposts / MilepostData) is kept available for
    # future use, but mileposts are not currently rendered on the map.

    # Extract industries
    print(f"  Extracting industries for route prefix {region_config.route_prefix}...")
    industries = extract_industries(
        str(industry_db_path),
        region_config.route_prefix,
        section_map,
        tile_geo_bounds,
        tile_based_config
    )
    print(f"  Extracted {len(industries)} industries")

    # Trains are placed later (globally, across all regions) by the caller.

    # Calculate bounds
    bounds = calculate_bounds(sections, signals, ai_locations, industries)

    # Convert tile bounds to TileData list
    tiles = []
    if use_tile_coords:
        # For tile-based mode, create tile data from tiles_involved
        for (tile_x, tile_z) in tiles_involved:
            # Calculate tile bounds in world coordinates
            x_offset = (tile_x - tile_based_config.home_tile[0]) * tile_based_config.tile_width
            z_offset = (tile_z - tile_based_config.home_tile[1]) * tile_based_config.tile_height  # Inverted

            tiles.append(TileData(
                x=tile_x,
                z=tile_z,
                lat_north=z_offset + tile_based_config.tile_height,  # y_max
                lat_south=z_offset,  # y_min
                lon_east=x_offset + tile_based_config.tile_width,  # x_max
                lon_west=x_offset,  # x_min
                is_corrected=False
            ))
    else:
        for (tile_x, tile_z), tile_bounds in tile_geo_bounds.items():
            lon_east, lon_west, lat_north, lat_south = tile_bounds
            tiles.append(TileData(
                x=tile_x,
                z=tile_z,
                lat_north=lat_north,
                lat_south=lat_south,
                lon_east=lon_east,
                lon_west=lon_west,
                is_corrected=(tile_x, tile_z) in corrected_tiles
            ))
    print(f"  Extracted {len(tiles)} tile boundaries ({len(corrected_tiles)} corrected)")

    return RegionData(
        id=region_config.id,
        display_name=region_config.display_name,
        sections=sections,
        signals=signals,
        ai_locations=ai_locations,
        industries=industries,
        tiles=tiles,
        trains=[],   # attached later by the caller (global cross-region placement)
        bounds=bounds
    )


if __name__ == '__main__':
    import sys
    from config_parser import parse_config

    if len(sys.argv) < 2:
        print("Usage: region_extractor.py <config.ini>")
        sys.exit(1)

    config = parse_config(sys.argv[1])
    tile_corrections = load_tile_corrections(str(config.tile_corrections))

    for region_config in config.regions:
        region_data = extract_region(
            region_config,
            config.industry_db,
            tile_corrections,
            str(config.terrain_tile_dir)
        )
        print(f"\nRegion {region_data.id} summary:")
        print(f"  Sections: {len(region_data.sections)}")
        print(f"  Signals: {len(region_data.signals)}")
        print(f"  AI Locations: {len(region_data.ai_locations)}")
        print(f"  Industries: {len(region_data.industries)}")
        if region_data.bounds:
            print(f"  Bounds: {region_data.bounds}")
