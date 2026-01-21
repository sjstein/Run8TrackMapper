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
TILE_DIR = r'C:\Run8Studios\Run8 Train Simulator V3\Content\V3Routes\Regions\SouthernCA\TerrainTiles'

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
class IndustryData:
    """Extracted industry data ready for JSON serialization"""
    tag: str
    name: str
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
    industries: List[IndustryData] = field(default_factory=list)
    tiles: List[TileData] = field(default_factory=list)
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
                     tile_dir: str = TILE_DIR) -> Tuple[Dict, Set]:
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


def convert_run8_to_latlon(x: float, z: float, tile_geo_bounds: Tuple[float, float, float, float]) -> Tuple[float, float]:
    """Convert Run8 coordinates to lat/lon"""
    lon_east, lon_west, lat_north, lat_south = tile_geo_bounds

    origin_lat = lat_south
    origin_lon = lon_west

    lat_offset_degrees = abs(z) / METERS_PER_DEGREE_LAT

    center_lat = (lat_south + lat_north) / 2.0
    meters_per_degree_lon = METERS_PER_DEGREE_LAT * math.cos(math.radians(center_lat))
    lon_offset_degrees = x / meters_per_degree_lon

    lat = origin_lat + lat_offset_degrees
    lon = origin_lon + lon_offset_degrees

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
    world_y = (tile_z - home_z) * tile_height + z

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


def find_end_tile(node: TrackNode, section: TrackSection) -> Tuple[int, int]:
    """Find the tile that contains the end position of a node

    For cross-tile sections, the end position may be on a different tile.
    We find this by matching the end position with another node's start position.
    """
    end_tile = node.tile_index  # Default to same tile

    for other_node in section.nodes:
        if other_node is node:
            continue
        # Check if other node's start position matches this node's end position
        if (abs(other_node.position[0] - node.end_position[0]) < 0.5 and
            abs(other_node.position[1] - node.end_position[1]) < 0.5 and
            abs(other_node.position[2] - node.end_position[2]) < 0.5):
            end_tile = other_node.tile_index
            break

    return end_tile


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

    for section in db.sections:
        # Select which nodes to plot
        nodes_to_plot = select_nodes_to_plot(section)

        all_paths = []  # List of paths, one per node
        total_length_m = 0.0

        for node in nodes_to_plot:
            start_tile = node.tile_index

            if use_tile_coords:
                # Tile-based coordinate mode
                # Find end tile for cross-tile sections
                end_tile = find_end_tile(node, section)

                # Convert to world tile coordinates
                start_x, start_y = convert_run8_to_tile_coords(
                    node.position[0], node.position[2],
                    start_tile,
                    tile_based_config.home_tile,
                    tile_based_config.tile_width,
                    tile_based_config.tile_height
                )
                end_x, end_y = convert_run8_to_tile_coords(
                    node.end_position[0], node.end_position[2],
                    end_tile,
                    tile_based_config.home_tile,
                    tile_based_config.tile_width,
                    tile_based_config.tile_height
                )

                # Check if curved
                is_curved = abs(node.radius_meters) > 0.1

                if is_curved:
                    # Interpolate curve in local coordinates directly
                    curve_points_local = interpolate_curve(
                        (node.position[0], node.position[1], node.position[2]),
                        (node.end_position[0], node.end_position[1], node.end_position[2]),
                        abs(node.radius_meters),
                        node.arcLen_meters,
                        node.curve_sign,
                        max(50, int(node.num_segments / 4)),
                        node.curve_deg
                    )
                    # Convert each point to world tile coordinates
                    path_points = []
                    for local_x, local_z in curve_points_local:
                        world_x, world_y = convert_run8_to_tile_coords(
                            local_x, local_z,
                            start_tile,
                            tile_based_config.home_tile,
                            tile_based_config.tile_width,
                            tile_based_config.tile_height
                        )
                        path_points.append((world_x, world_y))
                    total_length_m += node.arcLen_meters
                else:
                    # Straight segment
                    path_points = [(start_x, start_y), (end_x, end_y)]
                    # Calculate straight-line distance
                    dx = node.end_position[0] - node.position[0]
                    dy = node.end_position[1] - node.position[1]
                    dz = node.end_position[2] - node.position[2]
                    total_length_m += math.sqrt(dx*dx + dy*dy + dz*dz)

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
                end_tile = find_end_tile(node, section)
                if end_tile not in tile_geo_bounds:
                    end_tile = start_tile  # Fall back to start tile
                    if end_tile not in tile_geo_bounds:
                        continue

                # Convert start/end to lat/lon using correct tiles
                start_lat, start_lon = convert_run8_to_latlon(
                    node.position[0], node.position[2], tile_geo_bounds[start_tile]
                )
                end_lat, end_lon = convert_run8_to_latlon(
                    node.end_position[0], node.end_position[2], tile_geo_bounds[end_tile]
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
                    # Straight segment
                    path_points = [(start_lat, start_lon), (end_lat, end_lon)]
                    # Calculate straight-line distance
                    dx = node.end_position[0] - node.position[0]
                    dy = node.end_position[1] - node.position[1]
                    dz = node.end_position[2] - node.position[2]
                    total_length_m += math.sqrt(dx*dx + dy*dy + dz*dz)

                if len(path_points) >= 2:
                    # Filter out zero-length or near-zero-length paths (renders as circles)
                    seg_length = math.sqrt(
                        (path_points[-1][0] - path_points[0][0])**2 +
                        (path_points[-1][1] - path_points[0][1])**2
                    )
                    # Only add if segment length is meaningful (> ~1 meter in degrees)
                    if seg_length > 0.00001:
                        all_paths.append(path_points)

        if all_paths:
            length_ft = total_length_m * 3.28084

            sections.append(SectionData(
                id=section.index,
                paths=all_paths,
                length_ft=round(length_ft, 1),
                length_m=round(total_length_m, 1),
                is_switch=is_switch(section)
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
            is_advance_diverging=signal.is_advance_diverging
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

            x = start_node.position[0] + ratio * (end_node.end_position[0] - start_node.position[0])
            z = start_node.position[2] + ratio * (end_node.end_position[2] - start_node.position[2])

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

        # Use midpoint of first node
        x = (node.position[0] + node.end_position[0]) / 2
        z = (node.position[2] + node.end_position[2]) / 2

        if use_tile_coords:
            lat, lon = convert_run8_to_tile_coords(
                x, z, tile,
                tile_based_config.home_tile,
                tile_based_config.tile_width,
                tile_based_config.tile_height
            )
        else:
            lat, lon = convert_run8_to_latlon(x, z, bounds)

        industries.append(IndustryData(
            tag=industry.trk_sym,
            name=industry.name,
            lat=lat,
            lon=lon,
            track_sections=track_section_ids
        ))

    return industries


def calculate_bounds(sections: List[SectionData],
                     signals: List[SignalData],
                     ai_locations: List[AILocationData],
                     industries: List[IndustryData]) -> Optional[Tuple[Tuple[float, float], Tuple[float, float]]]:
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

    if not all_lats:
        return None

    return ((min(all_lats), min(all_lons)), (max(all_lats), max(all_lons)))


def extract_region(region_config: RegionConfig,
                   industry_db_path: Path,
                   tile_corrections: Dict[Tuple[int, int], Dict[str, float]],
                   tile_dir: str = None,
                   tile_based_config: Optional[TileBasedConfig] = None) -> RegionData:
    """Extract all data for a single region

    Args:
        region_config: Region configuration
        industry_db_path: Path to the industry database
        tile_corrections: Dict of tile corrections
        tile_dir: Override tile directory (uses region_config.terrain_tile_dir or TILE_DIR if None)
        tile_based_config: Configuration for tile-based coordinates (if provided, uses tile coords)
    """
    print(f"\nExtracting region: {region_config.display_name}")
    use_tile_coords = tile_based_config is not None

    if use_tile_coords:
        print(f"  Using tile-based coordinates (home_tile={tile_based_config.home_tile})")
    else:
        # Determine tile directory: explicit override > region config > global default
        if tile_dir is None:
            if region_config.terrain_tile_dir:
                tile_dir = str(region_config.terrain_tile_dir)
            else:
                tile_dir = TILE_DIR
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

    # Calculate bounds
    bounds = calculate_bounds(sections, signals, ai_locations, industries)

    # Convert tile bounds to TileData list
    tiles = []
    if use_tile_coords:
        # For tile-based mode, create tile data from tiles_involved
        for (tile_x, tile_z) in tiles_involved:
            # Calculate tile bounds in world coordinates
            x_offset = (tile_x - tile_based_config.home_tile[0]) * tile_based_config.tile_width
            z_offset = (tile_z - tile_based_config.home_tile[1]) * tile_based_config.tile_height
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
            tile_corrections
        )
        print(f"\nRegion {region_data.id} summary:")
        print(f"  Sections: {len(region_data.sections)}")
        print(f"  Signals: {len(region_data.signals)}")
        print(f"  AI Locations: {len(region_data.ai_locations)}")
        print(f"  Industries: {len(region_data.industries)}")
        if region_data.bounds:
            print(f"  Bounds: {region_data.bounds}")
