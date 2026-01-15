#!/usr/bin/env python
"""
Visualize a turnout and follow its branches to the next switches

Usage:
  python visualize_switch_network.py <start_section> <depth>

Example:
  python visualize_switch_network.py 66 2
"""

import folium
from folium.plugins import MousePosition
import math
import zlib
import struct
import os
import sys
import csv
import argparse
from explore_trackdb import TrackDatabase
from explore_signalHeadDb import SignalDatabase
from r8lib import IndustryFile, Industry, SpawnFile, SpawnPoint

# Constants
METERS_PER_DEGREE_LAT = 111320.0
TILE_DIR = r'C:\Run8Studios\Run8 Train Simulator V3\Content\V3Routes\Regions\SouthernCA\TerrainTiles'

# Geographic bounds for tile coordinate validation (covers entire USA)
LON_MIN = -130.0  # West coast (with margin)
LON_MAX = -65.0   # East coast (with margin)
LAT_MIN = 23.0    # Southern border (with margin)
LAT_MAX = 50.0    # Northern border (with margin)

def decompress_tr4(filepath):
    """Decompress a TR4 file using raw DEFLATE"""
    with open(filepath, 'rb') as f:
        compressed_data = f.read()
    try:
        return zlib.decompress(compressed_data, -zlib.MAX_WBITS)
    except Exception as e:
        print(f"Error decompressing {filepath}: {e}")
        return None

def find_tile_bounds(data, offset_e = 0.0, offset_n = 0.0):
    """Find geographic bounds in TR4 file data"""
    # Try sentinel-3 first
    for offset in range(0, len(data) - 44):
        try:
            sentinel = struct.unpack('<I', data[offset:offset+4])[0]
            if sentinel == 3:
                coord_offset = offset + 40
                if coord_offset + 16 <= len(data):
                    lon_east = struct.unpack('<f', data[coord_offset:coord_offset+4])[0] - offset_e
                    lon_west = struct.unpack('<f', data[coord_offset+4:coord_offset+8])[0] - offset_e
                    lat_north = struct.unpack('<f', data[coord_offset+8:coord_offset+12])[0] - offset_n
                    lat_south = struct.unpack('<f', data[coord_offset+12:coord_offset+16])[0] - offset_n

                    if (-180 < lon_east < 0 and -180 < lon_west < 0 and
                        0 < lat_north < 90 and 0 < lat_south < 90 and
                        abs(lon_east - lon_west) < 1 and abs(lat_north - lat_south) < 1 and
                        lat_south < lat_north and lon_west < lon_east):
                        return (lon_east, lon_west, lat_north, lat_south)
        except:
            pass

    # Try pattern matching for other formats
    for offset in range(0, len(data) - 16):
        try:
            lon_east = struct.unpack('<f', data[offset:offset+4])[0] - offset_e
            lon_west = struct.unpack('<f', data[offset+4:offset+8])[0] - offset_e
            lat_north = struct.unpack('<f', data[offset+8:offset+12])[0] - offset_n
            lat_south = struct.unpack('<f', data[offset+12:offset+16])[0] - offset_n

            # Validate coordinates are within USA bounds
            if (LON_MIN < lon_east < LON_MAX and LON_MIN < lon_west < LON_MAX and
                LAT_MIN < lat_north < LAT_MAX and LAT_MIN < lat_south < LAT_MAX and
                abs(lon_east - lon_west) < 0.05 and abs(lat_north - lat_south) < 0.05 and
                lon_west < lon_east and lat_south < lat_north):
                return (lon_east, lon_west, lat_north, lat_south)
        except:
            pass
    return None

def load_tile_corrections(csv_path='tile_corrections.csv'):
    """Load tile corrections from CSV file

    Returns:
        dict: Map of (tile_x, tile_z) -> {'lat_offset': float, 'lon_offset': float}
    """
    corrections = {}

    # Look for CSV in current directory and in src directory
    possible_paths = [
        csv_path,
        os.path.join(os.path.dirname(__file__), csv_path)
    ]

    csv_file = None
    for path in possible_paths:
        if os.path.exists(path):
            csv_file = path
            break

    if csv_file is None:
        return corrections

    try:
        with open(csv_file, 'r', newline='') as f:
            reader = csv.DictReader(f)
            for row in reader:
                tile_x = int(row['tile_x'])
                tile_z = int(row['tile_z'])
                corrections[(tile_x, tile_z)] = {
                    'lat_offset': float(row['lat_offset']),
                    'lon_offset': float(row['lon_offset'])
                }
        print(f'Loaded {len(corrections)} tile correction(s) from {csv_file}')
    except Exception as e:
        print(f'Warning: Failed to load tile corrections: {e}')

    return corrections

def load_tile_bounds(tiles_involved, tile_dir=TILE_DIR):
    """Load geographic bounds for all tiles involved

    Returns:
        tuple: (tile_geo_bounds dict, corrected_tiles set)
    """
    tile_geo_bounds = {}
    corrected_tiles = set()

    # Load tile corrections
    tile_corrections = load_tile_corrections()

    for tile_coords in sorted(tiles_involved):
        # Format: Always 5 digits for the number, with minus prepended for negatives
        # Negative: -XXXXX (6 chars), Positive: XXXXX (5 chars)
        x_str = f"{tile_coords[0]:06d}" if tile_coords[0] < 0 else f"{tile_coords[0]:05d}"
        y_str = f"{tile_coords[1]:06d}" if tile_coords[1] < 0 else f"{tile_coords[1]:05d}"
        filename = f"{x_str}_{y_str}.tr4"
        filepath = os.path.join(tile_dir, filename)

        if not os.path.exists(filepath):
            print(f'  WARNING: {filename} not found, skipping')
            continue

        data = decompress_tr4(filepath)
        if data is None:
            print(f'  WARNING: Failed to decompress {filename}')
            continue

        bounds = find_tile_bounds(data)

        if not bounds:
            print(f'  WARNING: No geographic bounds found in {filename}')
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
            print(f'  Applied correction to tile {tile_coords}: lat_offset={correction["lat_offset"]:.6f}, lon_offset={correction["lon_offset"]:.6f}')

        tile_geo_bounds[tile_coords] = bounds

    return tile_geo_bounds, corrected_tiles

def convert_run8_to_latlon(x, z, tile_geo_bounds):
    """Convert Run8 coordinates to lat/lon"""
    lon_east, lon_west, lat_north, lat_south = tile_geo_bounds

    # SW corner of tile is our origin point
    origin_lat = lat_south
    origin_lon = lon_west

    # Convert meter offsets to degree offsets
    lat_offset_degrees = abs(z) / METERS_PER_DEGREE_LAT

    # Longitude: varies by latitude (cos factor)
    center_lat = (lat_south + lat_north) / 2.0
    meters_per_degree_lon = METERS_PER_DEGREE_LAT * math.cos(math.radians(center_lat))
    lon_offset_degrees = x / meters_per_degree_lon

    # Apply offsets to origin
    lat = origin_lat + lat_offset_degrees
    lon = origin_lon + lon_offset_degrees

    return (lat, lon)

def point_match(p1, p2, tolerance=0.5):
    """Check if two points match within tolerance"""
    return math.sqrt((p1[0]-p2[0])**2 + (p1[1]-p2[1])**2) < tolerance

def interpolate_curve(start_pos, end_pos, radius, arc_length, curve_sign, num_segments=10):
    """Interpolate points along a curved track segment"""
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

    # Calculate center of circular arc
    h_squared = radius*radius - (chord_len/2.0)**2
    if h_squared < 0:
        return [(x1, z1), (x2, z2)]

    h = math.sqrt(h_squared)

    # Perpendicular to chord (rotated 90 degrees)
    perp_x = -dz / chord_len
    perp_z = dx / chord_len

    # Center point (offset from midpoint by h in perpendicular direction)
    mid_x = (x1 + x2) / 2.0
    mid_z = (z1 + z2) / 2.0

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

        # If arc indicates long sweep (>180°), adjust angle_diff accordingly
        if expected_angle > math.pi:
            # We need a long arc. Check if current angle_diff is going the right direction
            # but is the short path
            if abs(angle_diff) < math.pi:
                # Short path detected, extend to long path in the appropriate direction
                if angle_diff > 0:
                    # Extend positive direction: keep going positive past 180°
                    angle_diff = angle_diff - 2 * math.pi  # This makes it large and negative (long way)
                else:
                    # Extend negative direction: keep going negative past -180°
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

    # Use start point as origin for local tangent plane
    origin_lat = start_lat
    origin_lon = start_lon

    # For some reason (not sure why) we need to flip the curve sign for angles > 180
    if abs(curve_degrees) > 180:
        curve_sign = -curve_sign

    # Helper functions for local tangent plane coordinate conversion
    def latlon_to_local_meters(lat, lon):
        """Convert lat/lon to local meters (x=east, z=north)"""
        meters_north = (lat - origin_lat) * METERS_PER_DEGREE_LAT
        meters_per_degree_lon = METERS_PER_DEGREE_LAT * math.cos(math.radians(origin_lat))
        meters_east = (lon - origin_lon) * meters_per_degree_lon
        return (meters_east, meters_north)

    def local_meters_to_latlon(x, z):
        """Convert local meters (x=east, z=north) back to lat/lon"""
        lat = origin_lat + z / METERS_PER_DEGREE_LAT
        meters_per_degree_lon = METERS_PER_DEGREE_LAT * math.cos(math.radians(origin_lat))
        lon = origin_lon + x / meters_per_degree_lon
        return (lat, lon)

    # Convert start and end to local coordinates
    start_local = latlon_to_local_meters(start_lat, start_lon)
    end_local = latlon_to_local_meters(end_lat, end_lon)

    # Use interpolate_curve function in local meter space
    curve_points_local = interpolate_curve(
        (start_local[0], 0, start_local[1]),
        (end_local[0], 0, end_local[1]),
        radius,
        arc_length,
        -curve_sign,  # Negate to flip curve direction for positive-north z-axis
        num_segments
    )

    # Convert interpolated points back to lat/lon
    curve_points_latlon = []
    for x, z in curve_points_local:
        lat, lon = local_meters_to_latlon(x, z)
        curve_points_latlon.append((lat, lon))

    return curve_points_latlon

def create_signal_marker(lat, lon, rotation_y, is_absolute, signal_info, popup_html, layer):
    """Create a directional signal marker using triangle that scales with zoom

    Args:
        lat, lon: Geographic coordinates
        rotation_y: Rotation in degrees (0 = North, 90 = East, etc.)
        is_absolute: Boolean - True for absolute signals (different color)
        signal_info: String with signal details for tooltip
        popup_html: HTML content for popup
        layer: Folium layer to add markers to
    """
    # Color based on signal type
    if is_absolute:
        fill_color = '#FF6B35'  # Orange for absolute signals
        border_color = '#8B0000'  # Dark red border
    else:
        fill_color = '#FFD700'  # Gold for Intermediate signals
        border_color = '#800080'  # Purple border

    # Create triangle using geographic coordinates (scales with zoom)
    # Triangle size in degrees (approximately 15 meters)
    triangle_size = 0.00023

    # Convert rotation to radians and flip 180 degrees
    rotation_rad = math.radians(rotation_y) + math.pi

    # Calculate triangle vertices (pointing in direction of rotation_y)
    # Vertex 1: tip of triangle (pointing direction)
    v1_lat = lat + triangle_size * math.cos(rotation_rad)
    v1_lon = lon + triangle_size * math.sin(rotation_rad) / math.cos(math.radians(lat))

    # Vertices 2 and 3: base of triangle (perpendicular to pointing direction)
    base_angle_1 = rotation_rad + 2.5  # ~143 degrees offset
    base_angle_2 = rotation_rad - 2.5  # ~-143 degrees offset
    base_distance = triangle_size * 0.6

    v2_lat = lat + base_distance * math.cos(base_angle_1)
    v2_lon = lon + base_distance * math.sin(base_angle_1) / math.cos(math.radians(lat))

    v3_lat = lat + base_distance * math.cos(base_angle_2)
    v3_lon = lon + base_distance * math.sin(base_angle_2) / math.cos(math.radians(lat))

    # Draw triangle as a polygon
    triangle = folium.Polygon(
        locations=[(v1_lat, v1_lon), (v2_lat, v2_lon), (v3_lat, v3_lon)],
        color=border_color,
        fillColor=fill_color,
        fillOpacity=0.9,
        weight=2,
        popup=folium.Popup(popup_html, max_width=300),
        tooltip=signal_info
    )
    triangle.add_to(layer)

def get_spawn_type_name(type_num):
    """Convert spawn point type number to display name.

    TODO: Fill in actual type mappings when available.
    """
    return f"Type {type_num} ({type_num})"

def calculate_spawn_position(spawn, section, tile_geo_bounds):
    """Calculate lat/lon for a spawn point on a track section.

    Args:
        spawn: SpawnPoint object with track_id, dir, and unk4 (distance)
        section: TrackSection object
        tile_geo_bounds: Dict of tile coords -> geographic bounds

    Returns:
        Tuple (lat, lon) or None if position cannot be calculated
    """
    # Get distance from unk4 (stored as 4 bytes, interpret as float)
    distance = struct.unpack('<f', spawn.unk4)[0]

    # Get forward nodes (non-reverse paths)
    forward_nodes = [n for n in section.nodes if not n.is_reverse_path]
    if not forward_nodes:
        return None

    # dir indicates which node to measure from (0 or 1)
    node_idx = min(spawn.dir, len(forward_nodes) - 1)
    node = forward_nodes[node_idx]

    # Get tile bounds
    tile = (node.tile_index[0], node.tile_index[1])
    if tile not in tile_geo_bounds:
        return None

    # Get start and end positions
    start_pos = node.position
    end_pos = node.end_position

    # Calculate total length of the track segment
    if abs(node.radius_meters) > 0.1:
        # Curved track - use arc length
        total_length = node.arcLen_meters
    else:
        # Straight track - calculate distance
        dx = end_pos[0] - start_pos[0]
        dz = end_pos[2] - start_pos[2]
        total_length = math.sqrt(dx*dx + dz*dz)

    # Calculate interpolation factor (clamp to 0-1)
    t = min(1.0, distance / total_length) if total_length > 0 else 0

    # Interpolate position along the track
    # Note: This is a linear interpolation which works well for straight tracks
    # For curved tracks, this is an approximation
    x = start_pos[0] + t * (end_pos[0] - start_pos[0])
    z = start_pos[2] + t * (end_pos[2] - start_pos[2])

    # Convert to lat/lon
    return convert_run8_to_latlon(x, z, tile_geo_bounds[tile])

def is_switch(section):
    """Check if a section is a switch (has multiple unique paths)"""
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

def select_nodes_to_plot(section, section_idx, is_switch_section):
    """Select which nodes to plot based on section type and num_segments

    For non-switch sections with 2 nodes, only plot the node with num_segments > 0.
    Reports errors if both nodes have 0 or both have >0.

    Args:
        section: TrackSection object
        section_idx: Section index for error reporting
        is_switch_section: Boolean indicating if this is a switch

    Returns:
        tuple: (nodes_to_plot, error_message)
            nodes_to_plot: List of TrackNode objects to plot
            error_message: None if no error, string with error details otherwise
    """
    # Filter out reverse paths first
    forward_nodes = [n for n in section.nodes if not n.is_reverse_path]

    # Switch sections - plot all forward nodes (preserve existing behavior)
    if is_switch_section:
        return (forward_nodes, None)

    # Single forward node - plot it
    if len(forward_nodes) == 1:
        return (forward_nodes, None)

    # Two forward nodes - apply num_segments logic
    if len(forward_nodes) == 2:
        node0_segments = forward_nodes[0].num_segments
        node1_segments = forward_nodes[1].num_segments

        # ERROR: Both nodes have num_segments == 0
        if node0_segments == 0 and node1_segments == 0:
            error_msg = (f"Section {section_idx}: Both nodes have num_segments=0 "
                        f"(tiles {forward_nodes[0].tile_index}, {forward_nodes[1].tile_index})")
            return (forward_nodes, error_msg)

        # ERROR: Both nodes have num_segments > 0
        if node0_segments > 0 and node1_segments > 0:
            error_msg = (f"Section {section_idx}: Both nodes have num_segments>0 "
                        f"({node0_segments}, {node1_segments})")
            return (forward_nodes, error_msg)

        # VALID: Plot only the node with num_segments > 0
        if node0_segments > 0:
            return ([forward_nodes[0]], None)
        else:
            return ([forward_nodes[1]], None)

    # 3+ forward nodes (not a switch) or 0 nodes - plot all or nothing
    return (forward_nodes, None)

def find_connecting_section(current_section, exit_point, section_map, visited):
    """Find the next section that connects to the given exit point

    Uses the database's next_section field to get candidate sections (more reliable),
    then uses coordinate matching to determine which one connects at the specific exit point.
    """
    # First check the database's next_section field for candidate sections
    candidates = []
    for next_idx in current_section.next_section:
        if next_idx not in visited and next_idx in section_map:
            candidates.append((next_idx, section_map[next_idx]))

    # If we have candidates, check which one connects at the exit point
    # This handles switches where multiple sections connect at different endpoints
    best_match = None
    best_distance = float('inf')

    for next_idx, next_sec in candidates:
        for node in next_sec.nodes:
            start = (node.position[0], node.position[2])
            end = (node.end_position[0], node.end_position[2])

            # Calculate distances
            import math
            dist_start = math.sqrt((start[0]-exit_point[0])**2 + (start[1]-exit_point[1])**2)
            dist_end = math.sqrt((end[0]-exit_point[0])**2 + (end[1]-exit_point[1])**2)
            min_dist = min(dist_start, dist_end)

            if min_dist < 0.5 and min_dist < best_distance:
                best_distance = min_dist
                best_match = (next_idx, next_sec)

    return best_match if best_match else (None, None)

def follow_branch(start_section_idx, entry_point, section_map):
    """Follow a branch from a starting point until reaching the next switch"""
    path = [start_section_idx]
    visited = {start_section_idx}
    current_section = section_map[start_section_idx]
    current_point = entry_point

    # Check if the starting section itself is a switch
    if is_switch(current_section):
        print(f'  Found switch at section {start_section_idx}')
        return path

    while True:
        # Find exit point (the end that's not the entry point)
        exit_point = None
        for node in current_section.nodes:
            start = (node.position[0], node.position[2])
            end = (node.end_position[0], node.end_position[2])

            if point_match(start, current_point):
                exit_point = end
                break
            elif point_match(end, current_point):
                exit_point = start
                break

        if exit_point is None:
            print(f'  WARNING: Could not find exit point for section {current_section.index}')
            break

        # Find next connecting section
        next_idx, next_section = find_connecting_section(current_section, exit_point, section_map, visited)

        if next_idx is None:
            print(f'  Branch ends at section {current_section.index} (no connection)')
            break

        # Add to path
        path.append(next_idx)
        visited.add(next_idx)

        # Check if it's a switch
        if is_switch(next_section):
            print(f'  Found switch at section {next_idx}')
            break

        # Continue following
        current_section = next_section
        current_point = exit_point

    return path


def explore_switch_network(start_section_idx, section_map, depth=1):
    """
    Explore the switch network from a starting switch to a given depth

    Args:
        start_section_idx: Starting switch section
        section_map: Map of section indices to sections
        depth: How many levels of switches to traverse (1 = next switches, 2 = switches beyond those, etc.)

    Returns:
        Set of all section indices in the network
        List of terminal switch indices
    """
    all_sections = {start_section_idx}
    current_level_switches = [start_section_idx]

    for level in range(depth):
        print(f'Level {level + 1}: Exploring from {len(current_level_switches)} switch(es)')
        next_level_switches = []

        for switch_idx in current_level_switches:
            switch_section = section_map[switch_idx]

            # Find all unique endpoints of this switch
            endpoints = set()
            for node in switch_section.nodes:
                if not node.is_reverse_path:
                    start = (node.position[0], node.position[2])
                    end = (node.end_position[0], node.end_position[2])
                    endpoints.add(start)
                    endpoints.add(end)

            # Follow each branch from this switch
            for endpoint in endpoints:
                # Find connecting section
                next_idx, next_section = find_connecting_section(switch_section, endpoint, section_map, all_sections)

                if next_idx:
                    print(f'  Switch {switch_idx}, branch from ({endpoint[0]:.2f}, {endpoint[1]:.2f}):')
                    print(f'    -> Section {next_idx}')
                    path = follow_branch(next_idx, endpoint, section_map)
                    print(f'    Path: {path}')

                    # Add all sections in path
                    all_sections.update(path)

                    # Check if terminal section is a switch
                    terminal_idx = path[-1]
                    if is_switch(section_map[terminal_idx]):
                        next_level_switches.append(terminal_idx)
                        print(f'    Terminal: Section {terminal_idx} (switch)')
                    else:
                        print(f'    Terminal: Section {terminal_idx} (end of track)')
                    print()

        current_level_switches = next_level_switches
        if not current_level_switches:
            print(f'No more switches found at level {level + 2}')
            break

    return all_sections, current_level_switches

def main():
    # Parse command-line arguments
    parser = argparse.ArgumentParser(
        description='Visualize switch network from a starting turnout',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python visualize_switch_network.py 66 1   # Start at section 66, go 1 switch deep
  python visualize_switch_network.py 66 2   # Start at section 66, go 2 switches deep
  python visualize_switch_network.py 470 3  # Start at section 470, go 3 switches deep
        """
    )
    parser.add_argument('track_database', help='Track database filename')
    parser.add_argument('start_section', type=int,
                        help='Starting track section number (must be a turnout)')
    parser.add_argument('depth', type=int,
                        help='Number of switch levels to traverse (1, 2, 3, etc.)')
    parser.add_argument('--signal-db', default=None,
                        help='Optional: Signal database file (.r8) to visualize signals')
    parser.add_argument('--industry-db', default=None,
                        help='Optional: Industry database file (.ind) to visualize industry tracks')
    parser.add_argument('--route-prefix', type=int, default=None,
                        help='Required with --industry-db or --ai-locations: Route prefix to filter tracks')
    parser.add_argument('--ai-locations', default=None,
                        help='Optional: AI special locations file (.r8) to visualize spawn points')

    args = parser.parse_args()

    # Validate --route-prefix is provided if --industry-db or --ai-locations is used
    if args.industry_db and args.route_prefix is None:
        print('ERROR: --route-prefix is required when using --industry-db')
        return 1

    if args.ai_locations and args.route_prefix is None:
        print('ERROR: --route-prefix is required when using --ai-locations')
        return 1

    print('='*80)
    print('SWITCH NETWORK VISUALIZER')
    print('='*80)
    print()

    # Load track database
    print('Loading track database...')
    #db = TrackDatabase('TrackDatabase_BarstowYermo.r8')
    #db = TrackDatabase('TrackDatabase_Mojave.r8')
    db = TrackDatabase(args.track_database)

    section_map = {sec.index: sec for sec in db.sections}

    # Load signal database if provided
    signal_db = None
    if args.signal_db:
        print(f'Loading signal database: {args.signal_db}')
        try:
            signal_db = SignalDatabase(args.signal_db)
            print(f'Loaded {len(signal_db.signal_heads)} signal heads')
        except Exception as e:
            print(f'WARNING: Failed to load signal database: {e}')
            signal_db = None

    # Load industry database if provided
    industry_db = None
    industry_sections_map = {}  # Maps section_id -> list of industries using that section

    if args.industry_db:
        print(f'Loading industry database: {args.industry_db}')
        try:
            with open(args.industry_db, 'rb') as f:
                mem_map = f.read()

            # Parse industry file
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

                # Build section mapping - filter by route prefix
                if hasattr(industry, 'track') and industry.number_of_tracks > 0:
                    for track in industry.track:
                        # Only include tracks matching the specified route prefix
                        if track.route_prefix == args.route_prefix:
                            section_id = track.track_section
                            if section_id not in industry_sections_map:
                                industry_sections_map[section_id] = []
                            industry_sections_map[section_id].append(industry)

            industry_db = industry_file
            print(f'Loaded {industry_file.num_rec} industries')
            print(f'Filtered to route prefix {args.route_prefix}')
            print(f'Tracking {len(industry_sections_map)} track sections with matching industries')

        except Exception as e:
            print(f'WARNING: Failed to load industry database: {e}')
            import traceback
            traceback.print_exc()
            industry_db = None

    # Load AI special locations if provided
    spawn_db = None
    spawn_sections_map = {}  # Maps track_id -> list of spawn points

    if args.ai_locations:
        print(f'Loading AI special locations: {args.ai_locations}')
        try:
            with open(args.ai_locations, 'rb') as f:
                mem_map = f.read()

            # Parse spawn file
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

                # Filter by route prefix and build mapping
                if spawn.route_prefix == args.route_prefix:
                    track_id = spawn.track_id
                    if track_id not in spawn_sections_map:
                        spawn_sections_map[track_id] = []
                    spawn_sections_map[track_id].append(spawn)

            spawn_db = spawn_file
            print(f'Loaded {spawn_file.num_rec} AI special locations')
            print(f'Filtered to route prefix {args.route_prefix}: {len(spawn_sections_map)} track sections with spawn points')

        except Exception as e:
            print(f'WARNING: Failed to load AI special locations: {e}')
            import traceback
            traceback.print_exc()
            spawn_db = None

    # Validate starting section exists
    if args.start_section not in section_map:
        print(f'ERROR: Section {args.start_section} not found in track database')
        return 1

    # Validate starting section is a switch
    start_section = section_map[args.start_section]
    if not is_switch(start_section):
        print(f'ERROR: Section {args.start_section} is not a switch/turnout')
        print(f'  It has {len(start_section.nodes)} node(s)')
        print(f'  Switches must have multiple diverging paths')
        return 1

    # Validate depth
    if args.depth < 1:
        print(f'ERROR: Depth must be at least 1')
        return 1

    print(f'Starting from section {args.start_section}')
    print(f'Traversing {args.depth} level(s) of switches')
    print()

    # Explore the network
    all_sections, terminal_switches = explore_switch_network(args.start_section, section_map, args.depth)

    print(f'Total sections to visualize: {len(all_sections)}')
    print(f'Sections: {sorted(all_sections)}')
    print()


    # Find all tiles involved
    tiles_involved = set()
    for sec_idx in all_sections:
        section = section_map[sec_idx]
        for node in section.nodes:
            tiles_involved.add((node.tile_index[0], node.tile_index[1]))

    print(f'Loading tile bounds for {len(tiles_involved)} tiles...')
    tile_geo_bounds, corrected_tiles = load_tile_bounds(tiles_involved)

    print()

    # Create visualization
    print('Creating visualization...')

    # Calculate map center from all sections
    all_lats, all_lons = [], []
    for sec_idx in all_sections:
        section = section_map[sec_idx]
        for node in section.nodes:
            tile = (node.tile_index[0], node.tile_index[1])
            if tile in tile_geo_bounds:
                lat, lon = convert_run8_to_latlon(node.position[0], node.position[2], tile_geo_bounds[tile])
                all_lats.append(lat)
                all_lons.append(lon)

    center_lat = sum(all_lats) / len(all_lats) if all_lats else 34.9
    center_lon = sum(all_lons) / len(all_lons) if all_lons else -117.0

    # Create map
    m = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=16,
        max_zoom=22,
        tiles='OpenStreetMap'
    )


    # Add satellite layer with max zoom
    folium.TileLayer(
        tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
        attr='Esri',
        name='Satellite',
        overlay=False,
        control=True,
        show=False,  # Don't show by default - OpenStreetMap will be default
        max_zoom=22,  # Allow deeper zoom
        max_native_zoom=19  # Tiles available up to zoom 19, but allow scaling beyond
    ).add_to(m)


    # Add mouse position display (bottom right)
    MousePosition(
        position="bottomright",
        separator=" | ",
        prefix="Lat/Lon:",
        lat_formatter="function(num) {return L.Util.formatNum(num, 6);}",
        lng_formatter="function(num) {return L.Util.formatNum(num, 6);}",
    ).add_to(m)

# Add scale bar (bottom left)
    scale_script = """
<script>
(function() {
    function addScaleControl() {
        var mapName = '""" + m.get_name() + """';
        var mapObj = window[mapName];

        if (mapObj && L && L.control && L.control.scale) {
            try {
                L.control.scale({imperial: true, metric: true, position: 'bottomleft'}).addTo(mapObj);
            } catch (e) {
                console.error("Error adding scale control:", e);
                setTimeout(addScaleControl, 500);
            }
        } else {
            setTimeout(addScaleControl, 200);
        }
    }
    
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', addScaleControl);
    } else {
        addScaleControl();
    }
})();
</script>
"""
    m.get_root().html.add_child(folium.Element(scale_script))

# Add background opacity control
    opacity_control_js = '''
<div id="opacity-control" style="position: fixed;
            bottom: 80px; left: 10px; width: 200px;
            background-color: white; border:2px solid grey; z-index:9999;
            font-size:12px; padding: 8px; border-radius: 4px;">
<div style="margin-bottom: 5px; font-weight: bold;">Background Opacity</div>
<input type="range" id="opacity-slider" min="0" max="100" value="100"
       style="width: 100%;">
<div style="text-align: center; font-size: 11px; margin-top: 3px;">
    <span id="opacity-value">100</span>%
</div>
</div>

<script>
(function() {
    function initOpacityControl() {
        var mapName = "''' + m.get_name() + '''";
        var mapObj = window[mapName];

        if (!mapObj || typeof mapObj.eachLayer !== 'function') {
            setTimeout(initOpacityControl, 200);
            return;
        }

        try {
            // Store references to all base tile layers
            var baseLayers = [];
            mapObj.eachLayer(function(layer) {
                if (layer instanceof L.TileLayer) {
                    baseLayers.push(layer);
                }
            });

            console.log("Found", baseLayers.length, "base layers");

            // Function to update base layer opacity
            function updateBaseLayerOpacity(opacity) {
                var opacityValue = opacity / 100.0;

                baseLayers.forEach(function(layer) {
                    layer.setOpacity(opacityValue);
                });

                document.getElementById('opacity-value').textContent = opacity;
            }

            // Add event listener to slider
            var slider = document.getElementById('opacity-slider');
            if (slider) {
                slider.addEventListener('input', function(e) {
                    updateBaseLayerOpacity(e.target.value);
                });
            }
        } catch (e) {
            console.error("Error initializing opacity control:", e);
            setTimeout(initOpacityControl, 500);
        }
    }
    
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initOpacityControl);
    } else {
        setTimeout(initOpacityControl, 500);
    }
})();
</script>
'''
    m.get_root().html.add_child(folium.Element(opacity_control_js))

    # Draw tile boundaries (in a toggleable layer)
    tile_layer = folium.FeatureGroup(name='Tile Boundaries', show=False)

    for tile_coords, bounds in sorted(tile_geo_bounds.items()):
        lon_east, lon_west, lat_north, lat_south = bounds
        center_lat = (lat_north + lat_south) / 2
        center_lon = (lon_east + lon_west) / 2

        # Color corrected tiles red, normal tiles black
        tile_color = 'red' if tile_coords in corrected_tiles else 'black'
        tile_status = ' (CORRECTED)' if tile_coords in corrected_tiles else ''

        # Draw tile boundary rectangle
        folium.Rectangle(
            bounds=[[lat_south, lon_west], [lat_north, lon_east]],
            color=tile_color,
            fill=False,
            weight=2,
            opacity=0.7,
            popup=f"Tile {tile_coords}{tile_status}",
            tooltip=f"Tile {tile_coords}{tile_status}"
        ).add_to(tile_layer)

        # Draw center dot
        dot_color = 'red' if tile_coords in corrected_tiles else 'darkgrey'
        dot_fill = 'red' if tile_coords in corrected_tiles else 'white'
        folium.CircleMarker(
            location=[center_lat, center_lon],
            radius=3,
            popup=folium.Popup(
                f"<b>{tile_coords}{tile_status}</b><br>",
                max_width=300
            ),
            tooltip=f"Tile {tile_coords}{tile_status}",
            color=dot_color,
            fillColor=dot_fill,
            fillOpacity=1.0,
            weight=1
        ).add_to(tile_layer)

    tile_layer.add_to(m)

    # ===== Create Industries Layer =====
    industries_layer = folium.FeatureGroup(name='Industries', show=False)

    # ===== Create AI Locations Layer =====
    ai_locations_layer = folium.FeatureGroup(name='AI Locations', show=False)

    # ===== Plot Signals =====
    signal_metadata = []  # Collect signal data for JavaScript rendering
    if signal_db:
        print('Collecting signal data for dynamic rendering...')
        signals_layer = folium.FeatureGroup(name='Signals', show=False)

        signals_plotted = 0
        signals_skipped = 0

        for signal in signal_db.signal_heads:
            signal_tile = (signal.tile_xz[0], signal.tile_xz[1])

            # Filter: Only show signals on visualized tiles
            if signal_tile not in tile_geo_bounds:
                signals_skipped += 1
                continue

            # Convert signal position to lat/lon
            try:
                sig_lat, sig_lon = convert_run8_to_latlon(
                    signal.position[0],  # x coordinate
                    signal.position[2],  # z coordinate (position[1] is y/height)
                    tile_geo_bounds[signal_tile]
                )
            except Exception as e:
                print(f'  Warning: Failed to convert signal {signal.signal_index} coords: {e}')
                signals_skipped += 1
                continue

            # Build popup HTML with route summary
            routes_html = ""
            if signal.routes:
                routes_html = "<br><b>Routes:</b><br>"
                for i, route in enumerate(signal.routes[:5]):  # Limit to first 5
                    routes_html += (f"  {i+1}. {route.route_name} <br> MPH :{route.route_max_mph}<br>"
                                    f" Version: {route.version}<br> Diverging: {route.is_diverging}<br>"
                                    f" Speed class: {route.route_max_mph}<br>"
                                    f" Block Detectors: {route.block_detector_indices}<br>"
                                    f" Prev Signals: {route.prev_signal_indices}<br>")
                    if route.switch_connectors:
                        routes_html += f" Switch connectors:   {len(route.switch_connectors)}<br>"
                        for sc in route.switch_connectors:
                            routes_html += (f"   - Switch {sc.switch_index} "
                                            f"(clear_if_normal={sc.clear_if_thrown_normal})<br>")
                if len(signal.routes) > 5:
                    routes_html += f"  ... and {len(signal.routes) - 5} more"
            else:
                routes_html = "<br><i>No routes defined</i>"

            popup_html = f"""
            <b>Signal {signal.signal_index}</b><br>
            Model: {signal.model_name}<br>
            Type: {'Absolute' if signal.is_absolute else 'Intermediate'}<br>
            Dwarf: {'Yes' if signal.is_dwarf else 'No'}<br>
            Switch ind: {'Yes' if signal.is_switch_indicator else 'No'}<br>
            Advance Diverging: {'Yes' if signal.is_advance_diverging else 'No'}<br>
            {routes_html}
            """

            # Tooltip (hover text)
            tooltip_text = f"Signal {signal.signal_index}"

            # Store signal metadata for JavaScript rendering
            signal_metadata.append({
                'id': signal.signal_index,
                'lat': sig_lat,
                'lon': sig_lon,
                'rotation': signal.rotation_degrees_y,
                'is_absolute': signal.is_absolute,
                'popup_html': popup_html.replace('\n', ' ').replace("'", "\\'"),
                'tooltip': tooltip_text.replace("'", "\\'")
            })
            signals_plotted += 1

        signals_layer.add_to(m)

        # Add signal legend (with ID for visibility control)
        legend_html = '''
        <div id="signal-legend" style="position: fixed;
                    bottom: 150px; right: 10px; width: 180px; height: 110px;
                    background-color: white; border:2px solid grey; z-index:9999;
                    font-size:11px; padding: 8px; border-radius: 4px; display: none;">
        <div style="margin-bottom: 5px; font-weight: bold;">Signal Types</div>
        <div style="margin: 3px 0;">
            <span style="color: #FF6B35;">▲</span> Absolute Signal
        </div>
        <div style="margin: 3px 0;">
            <span style="color: #FFD700;">▲</span> Intermediate Signal
        </div>
        <div style="margin-top: 8px; font-size: 10px; color: #666;">
            Triangle shows signal facing direction
        </div>
        </div>
        '''
        m.get_root().html.add_child(folium.Element(legend_html))

        print(f'  Plotted {signals_plotted} signals on map')
        if signals_skipped > 0:
            print(f'  Skipped {signals_skipped} signals (not on visualized tiles)')
        print()

    # Draw each section
    # Color coding: red for start, blue for terminal switches, green for paths
    colors = {args.start_section: 'red'}  # Starting switch in red

    # Add all terminal switches in blue
    for term_switch in terminal_switches:
        colors[term_switch] = 'blue'

    # Also color the first-level terminal switches that are now intermediate
    # (We need to identify these from the exploration)
    # For now, mark all switches (except start) as blue
    for sec_idx in all_sections:
        if sec_idx != args.start_section and is_switch(section_map[sec_idx]):
            colors[sec_idx] = 'blue'

    # Track num_segments errors for summary
    section_errors = []

    # Build reverse mapping: industry -> list of section_ids
    # This allows us to find the center section for multi-section industries
    industry_to_sections = {}
    for sec_idx, industries in industry_sections_map.items():
        for industry in industries:
            if industry not in industry_to_sections:
                industry_to_sections[industry] = []
            industry_to_sections[industry].append(sec_idx)

    # Determine which section should receive the label for each industry
    # For industries with multiple sections, choose the middle one
    industry_label_sections = {}
    for industry, section_ids in industry_to_sections.items():
        if industry.trk_sym:  # Only label industries with symbols
            sorted_sections = sorted(section_ids)
            middle_idx = len(sorted_sections) // 2
            industry_label_sections[sorted_sections[middle_idx]] = industry

    for sec_idx in sorted(all_sections):
        section = section_map[sec_idx]

        # Determine color: industry tracks first, then switches, then regular
        is_industry_track = sec_idx in industry_sections_map
        if is_industry_track:
            color = 'purple'  # Industry track
        else:
            color = colors.get(sec_idx, 'green')  # Switches in red/blue, paths in green

        # Select nodes to plot based on section type
        section_is_switch = is_switch(section)
        nodes_to_plot, error_msg = select_nodes_to_plot(section, sec_idx, section_is_switch)

        # Handle errors
        if error_msg:
            section_errors.append(error_msg)
            print(f'  WARNING: {error_msg}')

        # Collect all points from all nodes in this section for label placement
        all_section_points = []

        # Draw the selected nodes
        for node in nodes_to_plot:

            # Get start tile
            start_tile = (node.tile_index[0], node.tile_index[1])
            if start_tile not in tile_geo_bounds:
                continue

            # Find end tile using reciprocal node matching
            end_tile = start_tile  # Default to same tile
            for other_node in section.nodes:
                if other_node is node:
                    continue
                if (abs(other_node.position[0] - node.end_position[0]) < 0.5 and
                    abs(other_node.position[1] - node.end_position[1]) < 0.5 and
                    abs(other_node.position[2] - node.end_position[2]) < 0.5):
                    end_tile = (other_node.tile_index[0], other_node.tile_index[1])
                    break

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
                # Interpolate curve
                points = interpolate_curve_geographic(
                    (start_lat, start_lon),
                    (end_lat, end_lon),
                    abs(node.radius_meters),
                    node.arcLen_meters,
                    node.curve_sign,
                    node.curve_deg,
                    num_segments=max(50, int(node.num_segments/4))
                )
            else:
                points = [(start_lat, start_lon), (end_lat, end_lon)]

            # Calculate length
            if is_curved:
                # Use arc length for curved sections
                length_meters = node.arcLen_meters
            else:
                # Calculate straight-line distance for straight sections
                dx = node.end_position[0] - node.position[0]
                dy = node.end_position[1] - node.position[1]
                dz = node.end_position[2] - node.position[2]
                length_meters = math.sqrt(dx*dx + dy*dy + dz*dz)

            # Convert to feet
            length_feet = length_meters * 3.28084

            # Draw path
            weight = 5 if sec_idx in colors else 3

            # Determine label based on section type with length
            section_type = ""
            if sec_idx == args.start_section:
                section_type = " (START)"
            elif is_switch(section):
                section_type = " (SWITCH)"

            # Build industry section details
            industry_html = ""
            if sec_idx in industry_sections_map:
                industry_html = "<br><b>Industries:</b><br>"
                for industry in industry_sections_map[sec_idx]:
                    industry_html += f"<b>• {industry.name}</b><br>"
                    if industry.local_name and industry.local_name != industry.name:
                        industry_html += f"  Local: {industry.local_name}<br>"
                    if industry.trk_sym:
                        industry_html += f"  Symbol: {industry.trk_sym}<br>"

            # Create detailed popup
            popup_html = f"""
            <b>Section {sec_idx}{section_type}</b><br>
            Length: {length_feet:.1f} ft ({length_meters:.1f} m)<br>
            Nodes: {len(section.nodes)}<br>
            Type: {'Curved' if is_curved else 'Straight'}<br>
            {'Radius: ' + f'{node.radius_meters:.1f} m<br>' if is_curved else ''}
            Track Type: {section.track_type}
            {industry_html}
            """

            # Build tooltip with industry information
            tooltip_text = f'Section {sec_idx}'
            if sec_idx in industry_sections_map:
                industries_on_section = industry_sections_map[sec_idx]
                tooltip_text += ' - INDUSTRY'
                for industry in industries_on_section[:3]:  # Limit to first 3
                    tooltip_text += f'\n{industry.name}'
                    if industry.trk_sym:
                        tooltip_text += f' ({industry.trk_sym})'
                if len(industries_on_section) > 3:
                    tooltip_text += f'\n... and {len(industries_on_section) - 3} more'

            # Collect points for label placement (if this is a label section)
            if sec_idx in industry_label_sections:
                all_section_points.extend(points)

            # For industry tracks, add to both layers (green on main, purple on industries layer)
            # For regular tracks, add only to main map
            if is_industry_track:
                # Add green version to main map (always visible)
                folium.PolyLine(
                    locations=points,
                    color='green',  # Always green on main map
                    weight=weight,
                    opacity=0.8,
                    popup=folium.Popup(popup_html, max_width=250),
                    tooltip=tooltip_text
                ).add_to(m)

                # Add purple version to industries layer (toggleable)
                folium.PolyLine(
                    locations=points,
                    color='purple',
                    weight=weight,
                    opacity=0.8,
                    popup=folium.Popup(popup_html, max_width=250),
                    tooltip=tooltip_text
                ).add_to(industries_layer)
            else:
                # Regular track - add to main map only
                folium.PolyLine(
                    locations=points,
                    color=color,
                    weight=weight,
                    opacity=0.8,
                    popup=folium.Popup(popup_html, max_width=250),
                    tooltip=tooltip_text
                ).add_to(m)

            # Add hash mark at end point (visible only at high zoom)
            # This creates a small perpendicular line to show section boundaries
            hash_length = 0.00002  # Small perpendicular offset in degrees

            # Calculate perpendicular direction (rotate 90 degrees from track direction)
            if is_curved and len(points) >= 2:
                # Use last segment direction for curved sections
                dx = points[-1][1] - points[-2][1]  # lon
                dy = points[-1][0] - points[-2][0]  # lat
            else:
                dx = end_lon - start_lon
                dy = end_lat - start_lat

            # Normalize and rotate 90 degrees
            length = math.sqrt(dx*dx + dy*dy)
            if length > 0.000001:
                perp_dx = -dy / length * hash_length
                perp_dy = dx / length * hash_length

                # Create short perpendicular line (hash mark)
                folium.PolyLine(
                    locations=[
                        [end_lat - perp_dy, end_lon - perp_dx],
                        [end_lat + perp_dy, end_lon + perp_dx]
                    ],
                    color='black',
                    weight=2,
                    opacity=0.8,
                    popup=f"Section {sec_idx} boundary"
                ).add_to(m)

        # After all nodes in this section are drawn, add industry label if needed
        if sec_idx in industry_label_sections and len(all_section_points) > 0:
            industry = industry_label_sections[sec_idx]
            # Calculate geometric center of all points in this entire section
            avg_lat = sum(p[0] for p in all_section_points) / len(all_section_points)
            avg_lon = sum(p[1] for p in all_section_points) / len(all_section_points)

            # Create a DivIcon marker with the industry symbol
            folium.Marker(
                location=[avg_lat, avg_lon],
                icon=folium.DivIcon(html=f'''
                    <div class="industry-label" style="
                        font-size: 14px;
                        font-weight: bold;
                        color: purple;
                        text-shadow: -1px -1px 0 #fff, 1px -1px 0 #fff, -1px 1px 0 #fff, 1px 1px 0 #fff;
                        white-space: nowrap;
                        text-align: center;
                    ">{industry.trk_sym}</div>
                ''')
            ).add_to(industries_layer)

    # Print error summary
    if section_errors:
        print(f'\n{"="*80}')
        print(f'ERRORS DETECTED: {len(section_errors)} section(s) with num_segments issues')
        print(f'{"="*80}')
        for error in section_errors:
            print(f'  - {error}')
        print(f'{"="*80}\n')

    # Add industries layer to map (if there are any industries)
    if industry_db and industry_sections_map:
        industries_layer.add_to(m)

    # ===== Plot AI Spawn Locations =====
    if spawn_db and spawn_sections_map:
        print('Plotting AI spawn locations...')
        spawns_plotted = 0
        spawns_skipped = 0

        for track_id, spawns in spawn_sections_map.items():
            # Only plot spawn points on tracks that are in our visualization
            if track_id not in all_sections:
                spawns_skipped += len(spawns)
                continue

            section = section_map[track_id]

            for spawn in spawns:
                pos = calculate_spawn_position(spawn, section, tile_geo_bounds)
                if pos is None:
                    spawns_skipped += 1
                    continue

                lat, lon = pos

                # Format time as HH:MM
                hours = spawn.time // 60
                minutes = spawn.time % 60
                time_str = f"{hours:02d}:{minutes:02d}"

                # Get type name
                type_name = get_spawn_type_name(spawn.type)

                # Create popup content
                popup_html = f"""
                <b>{spawn.name}</b><br>
                Type: {type_name}<br>
                Direction: {spawn.dir}<br>
                Time: {time_str}
                """

                # Create marker with yellow star emoji
                folium.Marker(
                    location=[lat, lon],
                    icon=folium.DivIcon(html='''
                        <div style="font-size: 20px; text-align: center;">&#11088;</div>
                    '''),
                    popup=folium.Popup(popup_html, max_width=200),
                    tooltip=spawn.name
                ).add_to(ai_locations_layer)

                spawns_plotted += 1

        ai_locations_layer.add_to(m)
        print(f'  Plotted {spawns_plotted} AI spawn locations')
        if spawns_skipped > 0:
            print(f'  Skipped {spawns_skipped} locations (not on visualized tracks or missing tile data)')

    # Add layer control
    folium.LayerControl(collapsed=False).add_to(m)

# Add zoom-based visibility for hash marks
    # Hash marks only appear at zoom level 19 and above
    zoom_control_script = """
<script>
(function() {
    function initHashMarks() {
        var mapName = '""" + m.get_name() + """';
        var mapObj = window[mapName];

        if (!mapObj || typeof mapObj.on !== 'function' || typeof mapObj.eachLayer !== 'function') {
            setTimeout(initHashMarks, 200);
            return;
        }

        try {
            // Function to update hash mark visibility based on zoom
            function updateHashMarks() {
                var zoom = mapObj.getZoom();
                var minZoom = 19;  // Hash marks appear at zoom 19+

                mapObj.eachLayer(function(layer) {
                    if (layer instanceof L.Polyline && layer.options.popup) {
                        var popup = layer.options.popup;
                        if (typeof popup === 'string' && popup.includes('boundary')) {
                            // This is a hash mark
                            if (zoom >= minZoom) {
                                layer.setStyle({opacity: 0.8});
                            } else {
                                layer.setStyle({opacity: 0});
                            }
                        }
                    }
                });
            }

            // Update on zoom change
            mapObj.on('zoomend', updateHashMarks);

            // Initial update
            updateHashMarks();
        } catch (e) {
            console.error("Error initializing hash marks:", e);
            setTimeout(initHashMarks, 500);
        }
    }
    
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initHashMarks);
    } else {
        setTimeout(initHashMarks, 500);
    }
})();
</script>
"""

    m.get_root().html.add_child(folium.Element(zoom_control_script))

# Add zoom-based scaling for industry labels
    industry_label_script = """
<script>
(function() {
    function initIndustryLabels() {
        var mapName = '""" + m.get_name() + """';
        var mapObj = window[mapName];

        if (!mapObj || typeof mapObj.on !== 'function') {
            setTimeout(initIndustryLabels, 200);
            return;
        }

        try {
            // Function to update industry label font size based on zoom
            function updateIndustryLabels() {
                var zoom = mapObj.getZoom();
                // Scale exponentially: base size 14px at zoom 16, scales with zoom
                // Formula: size = baseSize * (scaleFactor ^ (zoom - baseZoom))
                var baseSize = 14;
                var baseZoom = 16;
                var scaleFactor = 1.3;
                var fontSize = baseSize * Math.pow(scaleFactor, zoom - baseZoom);

                // Clamp between 8px and 40px
                fontSize = Math.max(8, Math.min(40, fontSize));

                var labels = document.querySelectorAll('.industry-label');
                labels.forEach(function(label) {
                    label.style.fontSize = fontSize + 'px';
                });
            }

            // Update on zoom change
            mapObj.on('zoomend', updateIndustryLabels);

            // Initial update
            updateIndustryLabels();
        } catch (e) {
            console.error("Error initializing industry labels:", e);
            setTimeout(initIndustryLabels, 500);
        }
    }
    
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initIndustryLabels);
    } else {
        setTimeout(initIndustryLabels, 500);
    }
})();
</script>
"""

    m.get_root().html.add_child(folium.Element(industry_label_script))

# Add Ctrl+Click selection functionality
    selection_script = """
<script>
(function() {
    function initTrackSelection() {
        var mapName = '""" + m.get_name() + """';
        var mapObj = window[mapName];

        if (!mapObj || typeof mapObj.eachLayer !== 'function') {
            setTimeout(initTrackSelection, 200);
            return;
        }

        try {

        // State management
        var selectedSections = new Map();
        var sectionPolylines = new Map();
        var sectionLengths = new Map();

        // Extract section ID from popup HTML
        function extractSectionId(popupHtml) {
            var match = popupHtml.match(/<b>Section (\\d+)/);
            return match ? parseInt(match[1]) : null;
        }

        // Extract length from popup HTML
        function extractLength(popupHtml) {
            var match = popupHtml.match(/Length: ([\\d.]+) ft \\(([\\d.]+) m\\)/);
            if (!match) return null;
            return {
                feet: parseFloat(match[1]),
                meters: parseFloat(match[2])
            };
        }

        // Build section maps
        var polylineCount = 0;
        var withPopupCount = 0;
        var firstPopupHtml = null;
        mapObj.eachLayer(function(layer) {
            // Check for Polyline but exclude Polygons (signals use Polygons)
            if (layer instanceof L.Polyline && !(layer instanceof L.Polygon)) {
                polylineCount++;
                var popup = layer.getPopup();
                if (!popup) return;
                withPopupCount++;

                var popupContent = popup.getContent();

                // Convert to string if it's a DOM element
                var popupHtml = '';
                if (typeof popupContent === 'string') {
                    popupHtml = popupContent;
                } else if (popupContent && popupContent.innerHTML !== undefined) {
                    popupHtml = popupContent.innerHTML;
                } else if (popupContent && popupContent.textContent !== undefined) {
                    popupHtml = popupContent.textContent;
                }

                // Store first popup for debugging
                if (!firstPopupHtml && popupHtml) {
                    firstPopupHtml = popupHtml;
                }

                if (popupHtml.includes('boundary')) return;

                var secId = extractSectionId(popupHtml);
                var length = extractLength(popupHtml);

                if (secId !== null) {
                    sectionPolylines.set(secId, layer);
                    if (length) sectionLengths.set(secId, length);
                }
            }
        });

        console.log('First popup HTML sample:', firstPopupHtml);

        console.log('Total Polylines:', polylineCount, 'With popups:', withPopupCount, 'Track sections found:', sectionPolylines.size);
        console.log('Section IDs:', Array.from(sectionPolylines.keys()).sort((a, b) => a - b));
        console.log('Sections with length data:', sectionLengths.size);

        // Attach click handlers
        var handlersAttached = 0;
        sectionPolylines.forEach(function(layer, secId) {
            layer.on('click', function(e) {
                console.log('Click detected on section', secId, 'Ctrl key:', e.originalEvent ? e.originalEvent.ctrlKey : 'no originalEvent');

                if (e.originalEvent && e.originalEvent.ctrlKey) {
                    // Use Leaflet's proper event stopping
                    L.DomEvent.stop(e);

                    // Close any open popup
                    mapObj.closePopup();

                    toggleSection(secId);

                    console.log('Ctrl+Click processed for section', secId);
                    return false;
                }
            });
            handlersAttached++;
        });
        console.log('Attached click handlers to', handlersAttached, 'sections');

        // Toggle section selection
        function toggleSection(secId) {
            var layer = sectionPolylines.get(secId);
            if (!layer) return;

            if (selectedSections.has(secId)) {
                // Deselect
                var original = selectedSections.get(secId);
                layer.setStyle(original);
                selectedSections.delete(secId);
            } else {
                // Select
                var original = {
                    color: layer.options.color,
                    weight: layer.options.weight,
                    opacity: layer.options.opacity
                };
                selectedSections.set(secId, original);
                layer.setStyle({
                    color: '#FFFF00',
                    weight: 8,
                    opacity: 1.0
                });
            }

            updateTotals();
        }

        // Calculate and display totals
        function updateTotals() {
            var totalMeters = 0;
            var totalFeet = 0;
            var sections = Array.from(selectedSections.keys()).sort((a, b) => a - b);

            sections.forEach(function(secId) {
                if (sectionLengths.has(secId)) {
                    var len = sectionLengths.get(secId);
                    totalMeters += len.meters;
                    totalFeet += len.feet;
                }
            });

            updateSummaryPanel(sections, totalMeters, totalFeet);
        }

        // Create/update summary panel
        function updateSummaryPanel(sections, totalMeters, totalFeet) {
            var panelId = 'track-selection-summary';
            var panel = document.getElementById(panelId);

            if (sections.length === 0) {
                if (panel) panel.remove();
                return;
            }

            if (!panel) {
                panel = document.createElement('div');
                panel.id = panelId;
                panel.style.cssText =
                    'position: fixed; top: 10px; right: 10px; width: 280px; ' +
                    'max-height: 400px; overflow-y: auto; background-color: white; ' +
                    'border: 2px solid #333; z-index: 9999; padding: 12px; ' +
                    'border-radius: 4px; font-family: Arial, sans-serif; font-size: 12px; ' +
                    'box-shadow: 0 2px 10px rgba(0,0,0,0.3);';
                document.body.appendChild(panel);
            }

            var html = '<div style="font-weight: bold; margin-bottom: 10px; font-size: 14px;">' +
                'Selected Track Sections</div>';

            html += '<div style="margin-bottom: 10px;">' +
                '<strong>Sections:</strong> ' + sections.join(', ') + '</div>';

            html += '<div style="margin-bottom: 10px; padding: 8px; ' +
                'background-color: #f0f0f0; border-radius: 3px;">' +
                '<strong>Total Length:</strong><br>' +
                '<div style="margin-top: 4px;">' +
                '  <span style="font-size: 14px; font-weight: bold;">' +
                totalFeet.toFixed(1) + '</span> feet<br>' +
                '  <span style="font-size: 14px; font-weight: bold;">' +
                totalMeters.toFixed(1) + '</span> meters' +
                '</div></div>';

            html += '<button id="clear-selection-btn" ' +
                'style="width: 100%; padding: 8px; cursor: pointer; ' +
                'background-color: #dc3545; color: white; border: none; ' +
                'border-radius: 3px; font-weight: bold; font-size: 12px;">' +
                'Clear Selection</button>';

            panel.innerHTML = html;

            document.getElementById('clear-selection-btn').onclick = function() {
                clearAllSelections();
            };
        }

        // Clear all selections
        function clearAllSelections() {
            selectedSections.forEach(function(original, secId) {
                var layer = sectionPolylines.get(secId);
                if (layer) layer.setStyle(original);
            });
            selectedSections.clear();

            var panel = document.getElementById('track-selection-summary');
            if (panel) panel.remove();
        }

console.log('Ctrl+Click selection functionality initialized');

        } catch (e) {
            console.error("Error initializing track selection:", e);
            setTimeout(initTrackSelection, 500);
        }
    }
    
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initTrackSelection);
    } else {
        setTimeout(initTrackSelection, 500);
    }
})();
</script>
"""

    m.get_root().html.add_child(folium.Element(selection_script))

    # Add dynamic signal rendering with zoom-dependent sizing
    if signal_db and signal_metadata:
        import json
        signal_data_json = json.dumps(signal_metadata)

        signal_rendering_script = """
        <script>
        (function() {
            function initSignalRendering() {
                var mapName = '""" + m.get_name() + """';
                var mapObj = window[mapName];
        
                if (!mapObj || typeof mapObj.eachLayer !== 'function') {
                    setTimeout(initSignalRendering, 200);
                    return;
                }
        
                try {
        
                // Signal metadata from Python
                var signalData = """ + signal_data_json + """;
        
                // Map to store signal triangle polygons
                var signalTriangles = new Map();  // id -> L.Polygon
        
                // Find the Folium-created Signals layer
                var signalsLayer = null;
                var foundExistingLayer = false;
        
                // Search for existing Signals layer
                var allFeatureGroups = [];
                mapObj.eachLayer(function(layer) {
                    if (layer instanceof L.FeatureGroup) {
                        var layerInfo = {
                            name: layer.options.name,
                            overlay_name: layer.options.overlay_name,
                            id: layer._leaflet_id
                        };
                        allFeatureGroups.push(layerInfo);
                    }
                });
        
                // Try to find by name in options
                mapObj.eachLayer(function(layer) {
                    if (layer instanceof L.FeatureGroup || layer instanceof L.LayerGroup) {
                        var name = layer.options.name || layer.options.overlay_name;
                        if (name === 'Signals') {
                            signalsLayer = layer;
                            foundExistingLayer = true;
                        }
                    }
                });
        
                // Try to find via layer control
                if (!signalsLayer && mapObj._controls) {
                    for (var i in mapObj._controls) {
                        var control = mapObj._controls[i];
                        if (control instanceof L.Control.Layers) {
                            if (control._layers) {
                                for (var layerId in control._layers) {
                                    var layerObj = control._layers[layerId];
                                    if (layerObj.name === 'Signals') {
                                        signalsLayer = layerObj.layer;
                                        foundExistingLayer = true;
                                        break;
                                    }
                                }
                            }
                            if (!signalsLayer && control._overlays) {
                                for (var name in control._overlays) {
                                    if (name === 'Signals') {
                                        signalsLayer = control._overlays[name];
                                        foundExistingLayer = true;
                                        break;
                                    }
                                }
                            }
                        }
                        if (signalsLayer) break;
                    }
                }
        
                // DOM-based search: find the Signals checkbox for manual wiring
                if (!signalsLayer) {
                    var layerControlDiv = document.querySelector('.leaflet-control-layers');
                    if (layerControlDiv) {
                        var checkboxes = layerControlDiv.querySelectorAll('input[type="checkbox"]');
                        checkboxes.forEach(function(checkbox) {
                            var label = checkbox.nextSibling;
                            var labelText = label ? label.textContent.trim() : '';
                            if (labelText === 'Signals') {
                                window.signalsCheckbox = checkbox;
                            }
                        });
                    }
                }
        
                // If still not found, create our own layer and wire it to the checkbox
                if (!signalsLayer) {
                    signalsLayer = L.featureGroup();
        
                    // Wire up the checkbox to control our layer
                    if (window.signalsCheckbox) {
                        // Set initial state to unchecked (hidden)
                        window.signalsCheckbox.checked = false;
        
                        // Get reference to signal legend
                        var signalLegend = document.getElementById('signal-legend');
        
                        // Add event listener to checkbox
                        window.signalsCheckbox.addEventListener('change', function() {
                            if (this.checked) {
                                mapObj.addLayer(signalsLayer);
                                if (signalLegend) signalLegend.style.display = 'block';
                            } else {
                                mapObj.removeLayer(signalsLayer);
                                if (signalLegend) signalLegend.style.display = 'none';
                            }
                        });
                    } else {
                        // Fallback: add layer to map if no checkbox found
                        signalsLayer.addTo(mapObj);
                        var signalLegend = document.getElementById('signal-legend');
                        if (signalLegend) signalLegend.style.display = 'block';
                    }
        
                    foundExistingLayer = false;
                }
        
                // Calculate triangle size based on zoom level (inverse scaling)
                function calculateTriangleSize(zoom) {
                    var baseSize = 0.00010;  // Base size at zoom 16
                    var scaleFactor = Math.pow(0.75, Math.max(0, zoom - 16));
                    return Math.max(0.00005, baseSize * scaleFactor);
                }
        
                // Calculate triangle vertices given center, rotation, and size
                function calculateTriangleVertices(lat, lon, rotationDeg, triangleSize) {
                    // Convert rotation to radians and flip 180 degrees
                    var rotationRad = (rotationDeg * Math.PI / 180.0) + Math.PI;
        
                    // Vertex 1: tip of triangle (pointing direction)
                    var v1_lat = lat + triangleSize * Math.cos(rotationRad);
                    var v1_lon = lon + triangleSize * Math.sin(rotationRad) / Math.cos(lat * Math.PI / 180.0);
        
                    // Vertices 2 and 3: base of triangle (perpendicular to pointing direction)
                    var baseAngle1 = rotationRad + 2.5;
                    var baseAngle2 = rotationRad - 2.5;
                    var baseDistance = triangleSize * 0.6;
        
                    var v2_lat = lat + baseDistance * Math.cos(baseAngle1);
                    var v2_lon = lon + baseDistance * Math.sin(baseAngle1) / Math.cos(lat * Math.PI / 180.0);
        
                    var v3_lat = lat + baseDistance * Math.cos(baseAngle2);
                    var v3_lon = lon + baseDistance * Math.sin(baseAngle2) / Math.cos(lat * Math.PI / 180.0);
        
                    return [
                        [v1_lat, v1_lon],
                        [v2_lat, v2_lon],
                        [v3_lat, v3_lon]
                    ];
                }
        
                // Create all signal triangles
                function createSignalTriangles(zoom) {
                    var triangleSize = calculateTriangleSize(zoom);
        
                    signalData.forEach(function(signal) {
                        // Determine colors based on signal type
                        var fillColor = signal.is_absolute ? '#FF6B35' : '#FFD700';
                        var borderColor = signal.is_absolute ? '#8B0000' : '#800080';
        
                        // Calculate vertices
                        var vertices = calculateTriangleVertices(
                            signal.lat,
                            signal.lon,
                            signal.rotation,
                            triangleSize
                        );
        
                        // Create polygon
                        var triangle = L.polygon(vertices, {
                            color: borderColor,
                            fillColor: fillColor,
                            fillOpacity: 0.9,
                            weight: 2
                        });
        
                        // Add popup and tooltip
                        triangle.bindPopup(signal.popup_html, {maxWidth: 300});
                        triangle.bindTooltip(signal.tooltip);
        
                        // Add to layer and store reference
                        triangle.addTo(signalsLayer);
                        signalTriangles.set(signal.id, triangle);
                    });
                }
        
                // Update signal triangle sizes based on zoom
                function updateSignalSizes() {
                    var zoom = mapObj.getZoom();
                    var triangleSize = calculateTriangleSize(zoom);
        
                    signalTriangles.forEach(function(polygon, signalId) {
                        // Find signal data
                        var signal = signalData.find(function(s) { return s.id === signalId; });
                        if (!signal) return;
        
                        // Recalculate vertices
                        var vertices = calculateTriangleVertices(
                            signal.lat,
                            signal.lon,
                            signal.rotation,
                            triangleSize
                        );
        
                        // Update polygon
                        polygon.setLatLngs(vertices);
                    });
                }
        
                // Create initial triangles
                var initialZoom = mapObj.getZoom();
                createSignalTriangles(initialZoom);
        
        // Update on zoom change
                    mapObj.on('zoomend', updateSignalSizes);
        
                } catch (e) {
                    console.error("Error initializing signal rendering:", e);
                    setTimeout(initSignalRendering, 500);
                }
            }
            
            if (document.readyState === 'loading') {
                document.addEventListener('DOMContentLoaded', initSignalRendering);
            } else {
                setTimeout(initSignalRendering, 1000);
            }
        })();
        </script>
        """
        m.get_root().html.add_child(folium.Element(signal_rendering_script))

    # Save map
    signal_suffix = '_with_signals' if args.signal_db else ''
    ai_suffix = '_with_ai' if args.ai_locations else ''
    output_file = f'{args.track_database.split(".")[0]}_{args.start_section}_depth{args.depth}{signal_suffix}{ai_suffix}.html'
    m.save(output_file)
    print(f'Map saved to: {output_file}')
    print()

if __name__ == '__main__':
    sys.exit(main())
