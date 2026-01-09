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

        # Create dict of tiles which are 'odd' in SoCal region
        exception_tiles = {'-00011,00040' : [0.009346, 0.006043],
                            '-00012,00040': [0.009346, 0.006043],
                            '-00013,00040': [0.009346, 0.006043],
                            '-00014,00040': [0.009346, 0.006043],
                            '-00015,00040': [0.009346, 0.006043],
                            '-00016,00041': [0.009346, 0.006043],
                            '-00005,00044': [0.007799, 0.001107],
                            '-00006,00044': [0.007799, 0.001107],
                            '-00007,00043': [0.009331, 0.002113]
                           }
        if int(y_str) >= 45:
            exception_tiles[f'{x_str},{y_str}'] = [0.007799, 0.001107]

        #if f'{x_str},{y_str}' in exception_tiles:
        if False:
            bounds = find_tile_bounds(data, exception_tiles[f'{x_str},{y_str}'][0],
                                      exception_tiles[f'{x_str},{y_str}'][1])
        else:
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

    args = parser.parse_args()

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
    setTimeout(function() {
        var mapName = '""" + m.get_name() + """';
        var mapObj = window[mapName];

        if (mapObj) {
            L.control.scale({imperial: true, metric: true, position: 'bottomleft'}).addTo(mapObj);
        } else {
            console.error("Map object not found for scale bar:", mapName);
        }
    }, 100);
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
    // Wait for map to be ready
    setTimeout(function() {
        var mapName = "''' + m.get_name() + '''";
        var mapObj = window[mapName];

        if (!mapObj) {
            console.error("Map object not found:", mapName);
            return;
        }

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
    }, 500); // Wait 500ms for map to fully initialize
})();
</script>
'''
    m.get_root().html.add_child(folium.Element(opacity_control_js))

    # Draw tile boundaries (in a toggleable layer)
    tile_layer = folium.FeatureGroup(name='Tile Boundaries', show=True)

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

    for sec_idx in sorted(all_sections):
        section = section_map[sec_idx]
        color = colors.get(sec_idx, 'green')  # Switches in red/blue, paths in green

        # Draw all non-reverse paths
        for node in section.nodes:
            if node.is_reverse_path:
                continue

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

            # Draw path
            weight = 5 if sec_idx in colors else 3

            # Determine label based on section type
            if sec_idx == args.start_section:
                label = f'Section {sec_idx} (START)'
            elif is_switch(section):
                label = f'Section {sec_idx} (SWITCH)'
            else:
                label = f'Section {sec_idx}'

            folium.PolyLine(
                locations=points,
                color=color,
                weight=weight,
                opacity=0.8,
                popup=label,
                tooltip=f'Section {sec_idx}'
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


    # Add layer control
    folium.LayerControl(collapsed=False).add_to(m)

    # Add zoom-based visibility for hash marks
    # Hash marks only appear at zoom level 19 and above
    zoom_control_script = """
<script>
(function() {
    setTimeout(function() {
        var mapName = '""" + m.get_name() + """';
        var mapObj = window[mapName];

        if (mapObj) {
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
        }
    }, 500);
})();
</script>
"""

    m.get_root().html.add_child(folium.Element(zoom_control_script))

    # Save map
    output_file = f'{args.track_database.split('.')[0]}_{args.start_section}_depth{args.depth}.html'
    m.save(output_file)
    print(f'Map saved to: {output_file}')
    print()

if __name__ == '__main__':
    sys.exit(main())
