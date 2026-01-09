#!/usr/bin/env python
"""
Tile Correction Management Tool

This tool helps identify and correct misaligned terrain tiles by:
1. Inspecting individual tiles and their neighbors
2. Scanning from a "home" tile to find misalignments
3. Maintaining a CSV of correction offsets

Usage:
  # Inspect a tile and its neighbors
  python manage_tile_corrections.py inspect -5 44

  # Manually add a correction
  python manage_tile_corrections.py add -5 44 --lat-offset 0.005 --lon-offset -0.002

  # Auto-calculate correction by aligning to neighbor
  python manage_tile_corrections.py add -5 44 --align-to -5,43 --edge south

  # Scan for misaligned tiles starting from track database
  python manage_tile_corrections.py scan --track-db TrackDatabase_Mojave.r8 --start-section 1880

  # Scan a specific tile range
  python manage_tile_corrections.py scan-range -10 -5 40 45 --home-tile -7,42

  # Auto-discover and scan all tiles in directory
  python manage_tile_corrections.py scan-all --home-tile -7,42
  python manage_tile_corrections.py scan-all --home-tile -7,42 --force
"""

import os
import sys
import csv
import argparse
import zlib
import struct
from collections import deque
from explore_trackdb import TrackDatabase

# Constants
TILE_DIR = r'C:\Run8Studios\Run8 Train Simulator V3\Content\V3Routes\Regions\SouthernCA\TerrainTiles'
DEFAULT_THRESHOLD = 0.0005  # degrees
DEFAULT_CSV = 'tile_corrections.csv'

def decompress_tr4(filepath):
    """Decompress a TR4 file using raw DEFLATE"""
    with open(filepath, 'rb') as f:
        compressed_data = f.read()
    try:
        return zlib.decompress(compressed_data, -zlib.MAX_WBITS)
    except Exception as e:
        return None

def find_tile_bounds(data):
    """Find geographic bounds in TR4 file data"""
    # Try sentinel-3 first
    for offset in range(0, len(data) - 44):
        try:
            sentinel = struct.unpack('<I', data[offset:offset+4])[0]
            if sentinel == 3:
                coord_offset = offset + 40
                if coord_offset + 16 <= len(data):
                    lon_east = struct.unpack('<f', data[coord_offset:coord_offset+4])[0]
                    lon_west = struct.unpack('<f', data[coord_offset+4:coord_offset+8])[0]
                    lat_north = struct.unpack('<f', data[coord_offset+8:coord_offset+12])[0]
                    lat_south = struct.unpack('<f', data[coord_offset+12:coord_offset+16])[0]

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
            lon_east = struct.unpack('<f', data[offset:offset+4])[0]
            lon_west = struct.unpack('<f', data[offset+4:offset+8])[0]
            lat_north = struct.unpack('<f', data[offset+8:offset+12])[0]
            lat_south = struct.unpack('<f', data[offset+12:offset+16])[0]

            if (-130 < lon_east < -65 and -130 < lon_west < -65 and
                23 < lat_north < 50 and 23 < lat_south < 50 and
                abs(lon_east - lon_west) < 0.05 and abs(lat_north - lat_south) < 0.05 and
                lon_west < lon_east and lat_south < lat_north):
                return (lon_east, lon_west, lat_north, lat_south)
        except:
            pass
    return None

def parse_tile_filename(filename):
    """
    Parse tile filename to extract X, Z coordinates.

    Args:
        filename (str): Tile filename (e.g., "00042_00035.tr4", "-00005_00044.tr4")

    Returns:
        tuple: (x, z) coordinates as integers, or None if invalid format

    Examples:
        >>> parse_tile_filename("00042_00035.tr4")
        (42, 35)
        >>> parse_tile_filename("-00005_00044.tr4")
        (-5, 44)
        >>> parse_tile_filename("-00030_-00014.tr4")
        (-30, -14)
        >>> parse_tile_filename("invalid.tr4")
        None
    """
    import re

    # Pattern matches: optional minus, 5-6 digits, underscore, optional minus, 5-6 digits, .tr4
    pattern = re.compile(r'^(-?\d{5,6})_(-?\d{5,6})\.tr4$')
    match = pattern.match(filename)

    if not match:
        return None

    try:
        x = int(match.group(1))
        z = int(match.group(2))
        return (x, z)
    except ValueError:
        return None

def load_tile_bounds_from_file(tile_x, tile_z, tile_dir=TILE_DIR):
    """Load bounds for a specific tile"""
    x_str = f"{tile_x:06d}" if tile_x < 0 else f"{tile_x:05d}"
    z_str = f"{tile_z:06d}" if tile_z < 0 else f"{tile_z:05d}"
    filename = f"{x_str}_{z_str}.tr4"
    filepath = os.path.join(tile_dir, filename)

    if not os.path.exists(filepath):
        return None

    data = decompress_tr4(filepath)
    if data is None:
        return None

    bounds = find_tile_bounds(data)
    if bounds is None:
        return None

    return {
        'lon_east': bounds[0],
        'lon_west': bounds[1],
        'lat_north': bounds[2],
        'lat_south': bounds[3],
        'exists': True
    }

def load_corrections_csv(csv_path):
    """Load existing corrections from CSV"""
    corrections = {}
    if not os.path.exists(csv_path):
        return corrections

    with open(csv_path, 'r', newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            tile_x = int(row['tile_x'])
            tile_z = int(row['tile_z'])
            corrections[(tile_x, tile_z)] = {
                'lat_offset': float(row['lat_offset']),
                'lon_offset': float(row['lon_offset']),
                'gap_before_correction': float(row.get('gap_before_correction', 0)),
                'notes': row.get('notes', '')
            }
    return corrections

def save_corrections_csv(corrections, csv_path):
    """Save corrections to CSV"""
    with open(csv_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['tile_x', 'tile_z', 'lat_offset', 'lon_offset', 'gap_before_correction', 'notes'])
        writer.writeheader()
        for (tile_x, tile_z), correction in sorted(corrections.items()):
            writer.writerow({
                'tile_x': tile_x,
                'tile_z': tile_z,
                'lat_offset': correction['lat_offset'],
                'lon_offset': correction['lon_offset'],
                'gap_before_correction': correction.get('gap_before_correction', 0),
                'notes': correction.get('notes', '')
            })

def discover_tiles_from_directory(tile_dir=TILE_DIR):
    """
    Discover all .tr4 tiles in directory and determine coordinate bounds.

    Args:
        tile_dir (str): Path to terrain tiles directory

    Returns:
        tuple: (tile_coords_set, x_bounds, z_bounds)
            - tile_coords_set: set of (x, z) tuples for all valid tiles
            - x_bounds: (x_min, x_max) tuple
            - z_bounds: (z_min, z_max) tuple

    Raises:
        IOError: If directory doesn't exist or isn't accessible

    Notes:
        - Silently skips malformed filenames
        - Skips subdirectories
        - Reports parsing statistics to stdout
    """
    import os

    # Validate directory
    if not os.path.exists(tile_dir):
        raise IOError(f"Tile directory not found: {tile_dir}")
    if not os.path.isdir(tile_dir):
        raise IOError(f"Not a directory: {tile_dir}")

    print(f"Scanning directory: {tile_dir}")

    # Discover tiles
    tile_coords = set()
    x_min = x_max = z_min = z_max = None
    files_scanned = 0
    files_skipped = 0

    try:
        for filename in os.listdir(tile_dir):
            files_scanned += 1

            # Skip non-files (e.g., Distant/ subdirectory)
            filepath = os.path.join(tile_dir, filename)
            if not os.path.isfile(filepath):
                files_skipped += 1
                continue

            # Parse filename
            coords = parse_tile_filename(filename)
            if coords is None:
                files_skipped += 1
                continue

            x, z = coords
            tile_coords.add((x, z))

            # Update bounds
            if x_min is None:
                x_min = x_max = x
                z_min = z_max = z
            else:
                x_min = min(x_min, x)
                x_max = max(x_max, x)
                z_min = min(z_min, z)
                z_max = max(z_max, z)

    except OSError as e:
        raise IOError(f"Error reading directory: {e}")

    if not tile_coords:
        raise IOError(f"No valid .tr4 tiles found in {tile_dir}")

    print(f"Scanned {files_scanned} files, found {len(tile_coords)} valid tiles")
    if files_skipped > 0:
        print(f"Skipped {files_skipped} non-tile files")
    print()

    return (tile_coords, (x_min, x_max), (z_min, z_max))

def calculate_gap(tile1_bounds, tile2_bounds, edge):
    """Calculate gap between two tiles along a specific edge

    Args:
        tile1_bounds: Bounds of first tile
        tile2_bounds: Bounds of second tile
        edge: Which edge to compare ('north', 'south', 'east', 'west')

    Returns:
        Gap in degrees (positive = gap, negative = overlap)
    """
    if edge == 'north':
        # tile2 is north of tile1: tile2.south should match tile1.north
        return tile2_bounds['lat_south'] - tile1_bounds['lat_north']
    elif edge == 'south':
        # tile2 is south of tile1: tile2.north should match tile1.south
        return tile1_bounds['lat_south'] - tile2_bounds['lat_north']
    elif edge == 'east':
        # tile2 is east of tile1: tile2.west should match tile1.east
        return tile2_bounds['lon_west'] - tile1_bounds['lon_east']
    elif edge == 'west':
        # tile2 is west of tile1: tile2.east should match tile1.west
        return tile1_bounds['lon_west'] - tile2_bounds['lon_east']

def get_neighbors(tile_x, tile_z):
    """Get coordinates and edge names for all 4 neighbors"""
    return [
        ((tile_x, tile_z + 1), 'north'),
        ((tile_x, tile_z - 1), 'south'),
        ((tile_x + 1, tile_z), 'east'),
        ((tile_x - 1, tile_z), 'west')
    ]

def find_home_tile(track_db_path, start_section_idx):
    """Find the home tile from a track database section"""
    db = TrackDatabase(track_db_path)
    section_map = {sec.index: sec for sec in db.sections}

    if start_section_idx not in section_map:
        print(f"ERROR: Section {start_section_idx} not found in database")
        return None

    section = section_map[start_section_idx]
    if not section.nodes:
        print(f"ERROR: Section {start_section_idx} has no nodes")
        return None

    # Use first node's tile
    node = section.nodes[0]
    tile_coords = (node.tile_index[0], node.tile_index[1])
    return tile_coords

# ============================================================================
# COMMANDS
# ============================================================================

def cmd_inspect(args):
    """Inspect a tile and its neighbors"""
    tile_x, tile_z = args.tile_x, args.tile_z

    print("="*80)
    print(f"INSPECTING TILE ({tile_x}, {tile_z})")
    print("="*80)
    print()

    # Load tile bounds
    bounds = load_tile_bounds_from_file(tile_x, tile_z)
    if bounds is None:
        print(f"ERROR: Could not load tile ({tile_x}, {tile_z})")
        return 1

    print(f"Tile ({tile_x}, {tile_z}):")
    print(f"  East:  {bounds['lon_east']:.6f}")
    print(f"  West:  {bounds['lon_west']:.6f}")
    print(f"  North: {bounds['lat_north']:.6f}")
    print(f"  South: {bounds['lat_south']:.6f}")
    print(f"  Width:  {abs(bounds['lon_east'] - bounds['lon_west']):.6f}°")
    print(f"  Height: {abs(bounds['lat_north'] - bounds['lat_south']):.6f}°")
    print()

    # Check all neighbors
    print("Neighbors:")
    print("-"*80)
    for (nx, nz), edge in get_neighbors(tile_x, tile_z):
        neighbor_bounds = load_tile_bounds_from_file(nx, nz)

        if neighbor_bounds is None:
            print(f"  {edge.upper():5s} ({nx:3d}, {nz:3d}): NOT FOUND")
            continue

        # Calculate gap
        gap = calculate_gap(bounds, neighbor_bounds, edge)
        gap_str = f"{abs(gap):.6f}° {'GAP' if gap > 0 else 'OVERLAP' if gap < 0 else 'ALIGNED'}"

        print(f"  {edge.upper():5s} ({nx:3d}, {nz:3d}): {gap_str}")

    print()
    print("="*80)
    return 0

def cmd_add(args):
    """Add a correction manually or by alignment"""
    tile_x, tile_z = args.tile_x, args.tile_z

    # Load existing corrections
    corrections = load_corrections_csv(args.output)

    # Check if correction already exists
    if (tile_x, tile_z) in corrections:
        if not args.force:
            print(f"ERROR: Correction for tile ({tile_x}, {tile_z}) already exists")
            print(f"       Use --force to overwrite")
            return 1
        print(f"WARNING: Overwriting existing correction for tile ({tile_x}, {tile_z})")

    # Manual mode
    if args.lat_offset is not None and args.lon_offset is not None:
        lat_offset = args.lat_offset
        lon_offset = args.lon_offset
        notes = args.notes or "Manual correction"
        gap = 0.0

    # Alignment mode
    elif args.align_to:
        # Parse align_to
        try:
            ref_x, ref_z = map(int, args.align_to.split(','))
        except:
            print(f"ERROR: Invalid --align-to format. Use: x,z (e.g., -5,43)")
            return 1

        if args.edge not in ['north', 'south', 'east', 'west']:
            print(f"ERROR: --edge must be one of: north, south, east, west")
            return 1

        # Load both tiles
        tile_bounds = load_tile_bounds_from_file(tile_x, tile_z)
        ref_bounds = load_tile_bounds_from_file(ref_x, ref_z)

        if tile_bounds is None:
            print(f"ERROR: Could not load tile ({tile_x}, {tile_z})")
            return 1
        if ref_bounds is None:
            print(f"ERROR: Could not load reference tile ({ref_x}, {ref_z})")
            return 1

        # Calculate gap
        gap = calculate_gap(ref_bounds, tile_bounds, args.edge)

        # Calculate correction to eliminate gap
        if args.edge in ['north', 'south']:
            lat_offset = -gap
            lon_offset = 0.0
        else:  # east or west
            lat_offset = 0.0
            lon_offset = -gap

        notes = args.notes or f"Aligned to tile ({ref_x}, {ref_z}) on {args.edge} edge"

        print(f"Gap detected: {abs(gap):.6f}° ({'gap' if gap > 0 else 'overlap'})")
        print(f"Correction: lat_offset={lat_offset:.6f}, lon_offset={lon_offset:.6f}")

    else:
        print("ERROR: Must specify either --lat-offset and --lon-offset, or --align-to and --edge")
        return 1

    # Add correction
    corrections[(tile_x, tile_z)] = {
        'lat_offset': lat_offset,
        'lon_offset': lon_offset,
        'gap_before_correction': abs(gap),
        'notes': notes
    }

    # Save
    save_corrections_csv(corrections, args.output)
    print(f"Correction saved to {args.output}")
    print(f"  Tile: ({tile_x}, {tile_z})")
    print(f"  lat_offset: {lat_offset:.6f}")
    print(f"  lon_offset: {lon_offset:.6f}")
    print(f"  Notes: {notes}")

    return 0

def cmd_scan(args):
    """Scan for misaligned tiles starting from home tile"""
    print("="*80)
    print("SCANNING FOR MISALIGNED TILES")
    print("="*80)
    print()

    # Find home tile
    print(f"Loading track database: {args.track_db}")
    home_tile = find_home_tile(args.track_db, args.start_section)
    if home_tile is None:
        return 1

    print(f"Home tile from section {args.start_section}: {home_tile}")
    print()

    # Load home tile bounds
    home_bounds = load_tile_bounds_from_file(home_tile[0], home_tile[1])
    if home_bounds is None:
        print(f"ERROR: Could not load home tile {home_tile}")
        return 1

    print(f"Home tile bounds:")
    print(f"  East:  {home_bounds['lon_east']:.6f}")
    print(f"  West:  {home_bounds['lon_west']:.6f}")
    print(f"  North: {home_bounds['lat_north']:.6f}")
    print(f"  South: {home_bounds['lat_south']:.6f}")
    print(f"Marked as TRUSTED (starting point)")
    print()

    # BFS expansion - PASS 1: Identify trusted tiles and tiles needing correction
    trusted = {home_tile: home_bounds}
    needs_correction = {}  # Map of tile -> bounds
    queue = deque([home_tile])
    visited = set([home_tile])
    tiles_checked = 0

    print(f"Pass 1: Identifying tiles (threshold: {args.threshold}°)...")
    print("-"*80)

    while queue:
        current_tile = queue.popleft()
        current_bounds = trusted[current_tile]
        tiles_checked += 1

        for (nx, nz), edge in get_neighbors(current_tile[0], current_tile[1]):
            if (nx, nz) in visited:
                continue
            visited.add((nx, nz))

            neighbor_bounds = load_tile_bounds_from_file(nx, nz)
            if neighbor_bounds is None:
                continue  # Tile doesn't exist

            # Calculate gap
            gap = calculate_gap(current_bounds, neighbor_bounds, edge)
            abs_gap = abs(gap)

            if abs_gap < args.threshold:
                # Aligned - trust this tile
                trusted[(nx, nz)] = neighbor_bounds
                queue.append((nx, nz))
                print(f"  Tile ({nx:3d}, {nz:3d}): Aligned ({abs_gap:.6f}°) - TRUSTED")
            else:
                # Misaligned - mark for correction (don't expand from it)
                if (nx, nz) not in needs_correction:
                    needs_correction[(nx, nz)] = neighbor_bounds
                    print(f"  Tile ({nx:3d}, {nz:3d}): GAP {abs_gap:.6f}° - NEEDS CORRECTION")

    print("-"*80)
    print()
    print(f"Pass 1 complete:")
    print(f"  Tiles checked: {tiles_checked}")
    print(f"  Trusted tiles: {len(trusted)}")
    print(f"  Tiles needing correction: {len(needs_correction)}")
    print()

    # PASS 2+: Iteratively calculate corrections
    # Start with trusted tiles, then add corrected tiles to expand coverage
    virtually_trusted = trusted.copy()
    remaining_to_correct = needs_correction.copy()
    corrections = {}
    corrections_added = 0
    pass_num = 2
    max_passes = args.max_passes

    while remaining_to_correct and pass_num <= max_passes + 1:
        print(f"Pass {pass_num}: Calculating corrections from {len(virtually_trusted)} trusted/corrected tiles...")
        print("-"*80)

        newly_corrected = {}
        tiles_to_remove = []

        for tile_coords, tile_bounds in remaining_to_correct.items():
            tile_x, tile_z = tile_coords

            # Collect corrections from each trusted neighbor
            lat_corrections = []
            lon_corrections = []
            contributing_neighbors = []

            # Check all 4 neighbors
            neighbors = [
                ((tile_x, tile_z + 1), 'north'),
                ((tile_x, tile_z - 1), 'south'),
                ((tile_x + 1, tile_z), 'east'),
                ((tile_x - 1, tile_z), 'west')
            ]

            for (nx, nz), edge in neighbors:
                if (nx, nz) not in virtually_trusted:
                    continue  # Not a trusted neighbor

                neighbor_bounds = virtually_trusted[(nx, nz)]

                # Calculate what this tile's edge should be to align with this neighbor
                if edge == 'north':
                    # This tile's north should match neighbor's south
                    target_north = neighbor_bounds['lat_south']
                    current_north = tile_bounds['lat_north']
                    lat_correction = target_north - current_north
                    lat_corrections.append(lat_correction)
                    contributing_neighbors.append(f"({nx},{nz})")

                    # Also align longitude midpoint (same column should have same lon center)
                    neighbor_mid_lon = (neighbor_bounds['lon_east'] + neighbor_bounds['lon_west']) / 2
                    current_mid_lon = (tile_bounds['lon_east'] + tile_bounds['lon_west']) / 2
                    lon_correction = neighbor_mid_lon - current_mid_lon
                    lon_corrections.append(lon_correction)

                elif edge == 'south':
                    # This tile's south should match neighbor's north
                    target_south = neighbor_bounds['lat_north']
                    current_south = tile_bounds['lat_south']
                    lat_correction = target_south - current_south
                    lat_corrections.append(lat_correction)
                    contributing_neighbors.append(f"({nx},{nz})")

                    # Also align longitude midpoint (same column should have same lon center)
                    neighbor_mid_lon = (neighbor_bounds['lon_east'] + neighbor_bounds['lon_west']) / 2
                    current_mid_lon = (tile_bounds['lon_east'] + tile_bounds['lon_west']) / 2
                    lon_correction = neighbor_mid_lon - current_mid_lon
                    lon_corrections.append(lon_correction)

                elif edge == 'east':
                    # This tile's east should match neighbor's west
                    target_east = neighbor_bounds['lon_west']
                    current_east = tile_bounds['lon_east']
                    lon_correction = target_east - current_east
                    lon_corrections.append(lon_correction)
                    contributing_neighbors.append(f"({nx},{nz})")

                    # Also align latitude midpoint (same row should have same lat center)
                    neighbor_mid_lat = (neighbor_bounds['lat_north'] + neighbor_bounds['lat_south']) / 2
                    current_mid_lat = (tile_bounds['lat_north'] + tile_bounds['lat_south']) / 2
                    lat_correction = neighbor_mid_lat - current_mid_lat
                    lat_corrections.append(lat_correction)

                elif edge == 'west':
                    # This tile's west should match neighbor's east
                    target_west = neighbor_bounds['lon_east']
                    current_west = tile_bounds['lon_west']
                    lon_correction = target_west - current_west
                    lon_corrections.append(lon_correction)
                    contributing_neighbors.append(f"({nx},{nz})")

                    # Also align latitude midpoint (same row should have same lat center)
                    neighbor_mid_lat = (neighbor_bounds['lat_north'] + neighbor_bounds['lat_south']) / 2
                    current_mid_lat = (tile_bounds['lat_north'] + tile_bounds['lat_south']) / 2
                    lat_correction = neighbor_mid_lat - current_mid_lat
                    lat_corrections.append(lat_correction)

            # Calculate final corrections (average of all suggestions)
            if lat_corrections or lon_corrections:
                final_lat_offset = sum(lat_corrections) / len(lat_corrections) if lat_corrections else 0.0
                final_lon_offset = sum(lon_corrections) / len(lon_corrections) if lon_corrections else 0.0

                # Calculate total gap magnitude
                total_gap = (final_lat_offset**2 + final_lon_offset**2)**0.5

                newly_corrected[tile_coords] = {
                    'lat_offset': final_lat_offset,
                    'lon_offset': final_lon_offset,
                    'gap_before_correction': total_gap,
                    'notes': f"Aligned to {len(contributing_neighbors)} neighbor(s): {', '.join(contributing_neighbors)}"
                }
                corrections_added += 1
                print(f"  Tile ({tile_x:3d}, {tile_z:3d}): lat={final_lat_offset:+.6f}, lon={final_lon_offset:+.6f} (from {len(contributing_neighbors)} neighbors)")

        # If no corrections made this pass, we're done
        if not newly_corrected:
            print("  No new corrections found")
            break

        # Add newly corrected tiles to corrections dict
        corrections.update(newly_corrected)

        # Apply corrections to bounds and add to virtually_trusted
        for tile_coords, correction in newly_corrected.items():
            original_bounds = remaining_to_correct[tile_coords]
            corrected_bounds = {
                'lon_east': original_bounds['lon_east'] + correction['lon_offset'],
                'lon_west': original_bounds['lon_west'] + correction['lon_offset'],
                'lat_north': original_bounds['lat_north'] + correction['lat_offset'],
                'lat_south': original_bounds['lat_south'] + correction['lat_offset'],
                'exists': True
            }
            virtually_trusted[tile_coords] = corrected_bounds

        # Remove corrected tiles from remaining_to_correct
        for tile_coords in newly_corrected.keys():
            del remaining_to_correct[tile_coords]

        # Re-evaluate uncategorized tiles if they exist (only for scan-range)
        if 'still_uncategorized' in locals():
            newly_categorized_trusted = {}
            newly_categorized_correction = {}

            for tile_coords, tile_bounds in list(still_uncategorized.items()):
                tile_x, tile_z = tile_coords

                # Check alignment with all 4 neighbors
                is_aligned = False
                max_gap = 0.0
                has_trusted_neighbor = False

                for (nx, nz), edge in get_neighbors(tile_x, tile_z):
                    # Check against virtually_trusted neighbors
                    if (nx, nz) not in virtually_trusted:
                        continue

                    has_trusted_neighbor = True
                    neighbor_bounds = virtually_trusted[(nx, nz)]
                    gap = calculate_gap(tile_bounds, neighbor_bounds, edge)
                    abs_gap = abs(gap)
                    max_gap = max(max_gap, abs_gap)

                    if abs_gap < args.threshold:
                        # Aligned with this trusted neighbor
                        is_aligned = True
                        break

                if is_aligned:
                    newly_categorized_trusted[tile_coords] = tile_bounds
                    virtually_trusted[tile_coords] = tile_bounds
                elif has_trusted_neighbor:
                    # Has trusted neighbors but not aligned
                    newly_categorized_correction[tile_coords] = tile_bounds
                    remaining_to_correct[tile_coords] = tile_bounds

            # Remove newly categorized tiles from still_uncategorized
            for tile_coords in newly_categorized_trusted:
                del still_uncategorized[tile_coords]
            for tile_coords in newly_categorized_correction:
                del still_uncategorized[tile_coords]

            if newly_categorized_trusted or newly_categorized_correction:
                print(f"  Re-categorized {len(newly_categorized_trusted)} as trusted, {len(newly_categorized_correction)} as needing correction")

        print("-"*80)
        print(f"Pass {pass_num} complete: {len(newly_corrected)} corrections added")
        print(f"Remaining tiles to correct: {len(remaining_to_correct)}")
        if 'still_uncategorized' in locals() and still_uncategorized:
            print(f"Uncategorized: {len(still_uncategorized)}")
        print()

        pass_num += 1

    if pass_num > max_passes + 1:
        print(f"WARNING: Reached maximum passes ({max_passes}). {len(remaining_to_correct)} tiles still need correction.")
        print()

    print("-"*80)
    print()
    print(f"Scan complete:")
    print(f"  Tiles checked: {tiles_checked}")
    print(f"  Trusted tiles: {len(trusted)}")
    print(f"  Corrections added: {corrections_added}")
    print(f"  Total corrections: {len(corrections)}")

    if remaining_to_correct:
        print(f"  WARNING: {len(remaining_to_correct)} tiles still need correction")
        print(f"           Consider increasing --max-passes (current: {max_passes})")

    if corrections_added > 0:
        save_corrections_csv(corrections, args.output)
        print(f"\nSaved to: {args.output}")

    return 0

def _scan_tile_range_core(x_min, x_max, z_min, z_max, home_tile,
                          threshold, max_passes, output, tile_dir=TILE_DIR):
    """
    Core multi-pass tile scanning and correction logic.

    This function contains the shared logic for scanning a tile range,
    categorizing tiles, and calculating corrections. Used by both
    scan-range and scan-all commands.

    Args:
        x_min, x_max (int): X coordinate range (inclusive)
        z_min, z_max (int): Z coordinate range (inclusive)
        home_tile (tuple): (x, z) coordinates of trusted home tile
        threshold (float): Gap threshold in degrees
        max_passes (int): Maximum correction passes
        output (str): Output CSV filepath
        tile_dir (str): Tile directory path

    Returns:
        tuple: (corrections_dict, statistics_dict)
            corrections_dict: {(x,z): {'lat_offset', 'lon_offset', 'gap_before_correction', 'notes'}}
            statistics_dict: {
                'total_tiles_loaded': int,
                'tiles_checked': int,
                'trusted_tiles': int,
                'corrections_added': int,
                'remaining_to_correct': int,
                'still_uncategorized': int,
                'passes_completed': int
            }
    """
    # Load home tile bounds
    home_bounds = load_tile_bounds_from_file(home_tile[0], home_tile[1], tile_dir)
    if home_bounds is None:
        raise ValueError(f"Could not load home tile {home_tile}")

    print(f"Home tile bounds:")
    print(f"  East:  {home_bounds['lon_east']:.6f}")
    print(f"  West:  {home_bounds['lon_west']:.6f}")
    print(f"  North: {home_bounds['lat_north']:.6f}")
    print(f"  South: {home_bounds['lat_south']:.6f}")
    print(f"Marked as TRUSTED (starting point)")
    print()

    # PASS 1: Scan all tiles in the range and categorize them
    trusted = {home_tile: home_bounds}
    needs_correction = {}  # Map of tile -> bounds
    uncategorized = {}  # Map of tile -> bounds for tiles not yet categorized

    print(f"Pass 1: Loading all tiles in range...")
    print("-"*80)

    # First, load all tiles in the range
    for z in range(z_min, z_max + 1):
        for x in range(x_min, x_max + 1):
            if (x, z) == home_tile:
                continue  # Already loaded as trusted
            tile_bounds = load_tile_bounds_from_file(x, z, tile_dir)
            if tile_bounds is not None:
                uncategorized[(x, z)] = tile_bounds

    total_tiles_loaded = len(uncategorized) + 1  # +1 for home tile
    print(f"Loaded {total_tiles_loaded} tiles (including home tile)")
    print()

    # Iteratively categorize tiles until no more changes
    categorization_pass = 1
    while uncategorized:
        print(f"Categorization pass {categorization_pass}:")
        newly_trusted = {}
        newly_needing_correction = {}

        for tile_coords, tile_bounds in uncategorized.items():
            tile_x, tile_z = tile_coords

            # Check alignment with all 4 neighbors
            is_aligned = False
            max_gap = 0.0
            has_trusted_neighbor = False

            for (nx, nz), edge in get_neighbors(tile_x, tile_z):
                # Only check against trusted neighbors
                if (nx, nz) not in trusted:
                    continue

                has_trusted_neighbor = True
                neighbor_bounds = trusted[(nx, nz)]
                gap = calculate_gap(tile_bounds, neighbor_bounds, edge)
                abs_gap = abs(gap)
                max_gap = max(max_gap, abs_gap)

                if abs_gap < threshold:
                    # Aligned with this trusted neighbor
                    is_aligned = True
                    break

            if is_aligned:
                newly_trusted[tile_coords] = tile_bounds
                print(f"  Tile ({tile_x:3d}, {tile_z:3d}): Aligned - TRUSTED")
            elif has_trusted_neighbor:
                # Has trusted neighbors but not aligned
                newly_needing_correction[tile_coords] = tile_bounds
                print(f"  Tile ({tile_x:3d}, {tile_z:3d}): GAP {max_gap:.6f}° - NEEDS CORRECTION")

        # Update sets
        trusted.update(newly_trusted)
        needs_correction.update(newly_needing_correction)

        # Remove categorized tiles from uncategorized
        for tile_coords in newly_trusted:
            del uncategorized[tile_coords]
        for tile_coords in newly_needing_correction:
            del uncategorized[tile_coords]

        print(f"  Pass {categorization_pass}: {len(newly_trusted)} trusted, {len(newly_needing_correction)} need correction, {len(uncategorized)} remaining")
        print()

        # If no tiles were categorized, we're done
        if not newly_trusted and not newly_needing_correction:
            if uncategorized:
                print(f"  {len(uncategorized)} tiles have no trusted neighbors and cannot be categorized")
            break

        categorization_pass += 1

    tiles_checked = len(trusted) + len(needs_correction)

    print("-"*80)
    print()
    print(f"Pass 1 complete:")
    print(f"  Tiles checked: {tiles_checked}")
    print(f"  Trusted tiles: {len(trusted)}")
    print(f"  Tiles needing correction: {len(needs_correction)}")
    if uncategorized:
        print(f"  Uncategorized tiles (no trusted neighbors yet): {len(uncategorized)}")
    print()

    # PASS 2+: Iteratively calculate corrections and re-evaluate uncategorized tiles
    # Start with trusted tiles, then add corrected tiles to expand coverage
    virtually_trusted = trusted.copy()
    remaining_to_correct = needs_correction.copy()
    still_uncategorized = uncategorized.copy()
    corrections = {}
    corrections_added = 0
    pass_num = 2

    while (remaining_to_correct or still_uncategorized) and pass_num <= max_passes + 1:
        print(f"Pass {pass_num}: Calculating corrections from {len(virtually_trusted)} trusted/corrected tiles...")
        print("-"*80)

        newly_corrected = {}
        tiles_to_remove = []

        for tile_coords, tile_bounds in remaining_to_correct.items():
            tile_x, tile_z = tile_coords

            # Collect corrections from each trusted neighbor
            lat_corrections = []
            lon_corrections = []
            contributing_neighbors = []

            # Check all 4 neighbors
            neighbors = [
                ((tile_x, tile_z + 1), 'north'),
                ((tile_x, tile_z - 1), 'south'),
                ((tile_x + 1, tile_z), 'east'),
                ((tile_x - 1, tile_z), 'west')
            ]

            for (nx, nz), edge in neighbors:
                if (nx, nz) not in virtually_trusted:
                    continue  # Not a trusted neighbor

                neighbor_bounds = virtually_trusted[(nx, nz)]

                # Calculate what this tile's edge should be to align with this neighbor
                if edge == 'north':
                    # This tile's north should match neighbor's south
                    target_north = neighbor_bounds['lat_south']
                    current_north = tile_bounds['lat_north']
                    lat_correction = target_north - current_north
                    lat_corrections.append(lat_correction)
                    contributing_neighbors.append(f"({nx},{nz})")

                    # Also align longitude midpoint (same column should have same lon center)
                    neighbor_mid_lon = (neighbor_bounds['lon_east'] + neighbor_bounds['lon_west']) / 2
                    current_mid_lon = (tile_bounds['lon_east'] + tile_bounds['lon_west']) / 2
                    lon_correction = neighbor_mid_lon - current_mid_lon
                    lon_corrections.append(lon_correction)

                elif edge == 'south':
                    # This tile's south should match neighbor's north
                    target_south = neighbor_bounds['lat_north']
                    current_south = tile_bounds['lat_south']
                    lat_correction = target_south - current_south
                    lat_corrections.append(lat_correction)
                    contributing_neighbors.append(f"({nx},{nz})")

                    # Also align longitude midpoint (same column should have same lon center)
                    neighbor_mid_lon = (neighbor_bounds['lon_east'] + neighbor_bounds['lon_west']) / 2
                    current_mid_lon = (tile_bounds['lon_east'] + tile_bounds['lon_west']) / 2
                    lon_correction = neighbor_mid_lon - current_mid_lon
                    lon_corrections.append(lon_correction)

                elif edge == 'east':
                    # This tile's east should match neighbor's west
                    target_east = neighbor_bounds['lon_west']
                    current_east = tile_bounds['lon_east']
                    lon_correction = target_east - current_east
                    lon_corrections.append(lon_correction)
                    contributing_neighbors.append(f"({nx},{nz})")

                    # Also align latitude midpoint (same row should have same lat center)
                    neighbor_mid_lat = (neighbor_bounds['lat_north'] + neighbor_bounds['lat_south']) / 2
                    current_mid_lat = (tile_bounds['lat_north'] + tile_bounds['lat_south']) / 2
                    lat_correction = neighbor_mid_lat - current_mid_lat
                    lat_corrections.append(lat_correction)

                elif edge == 'west':
                    # This tile's west should match neighbor's east
                    target_west = neighbor_bounds['lon_east']
                    current_west = tile_bounds['lon_west']
                    lon_correction = target_west - current_west
                    lon_corrections.append(lon_correction)
                    contributing_neighbors.append(f"({nx},{nz})")

                    # Also align latitude midpoint (same row should have same lat center)
                    neighbor_mid_lat = (neighbor_bounds['lat_north'] + neighbor_bounds['lat_south']) / 2
                    current_mid_lat = (tile_bounds['lat_north'] + tile_bounds['lat_south']) / 2
                    lat_correction = neighbor_mid_lat - current_mid_lat
                    lat_corrections.append(lat_correction)

            # Calculate final corrections (average of all suggestions)
            if lat_corrections or lon_corrections:
                final_lat_offset = sum(lat_corrections) / len(lat_corrections) if lat_corrections else 0.0
                final_lon_offset = sum(lon_corrections) / len(lon_corrections) if lon_corrections else 0.0

                # Calculate total gap magnitude
                total_gap = (final_lat_offset**2 + final_lon_offset**2)**0.5

                newly_corrected[tile_coords] = {
                    'lat_offset': final_lat_offset,
                    'lon_offset': final_lon_offset,
                    'gap_before_correction': total_gap,
                    'notes': f"Aligned to {len(contributing_neighbors)} neighbor(s): {', '.join(contributing_neighbors)}"
                }
                corrections_added += 1
                print(f"  Tile ({tile_x:3d}, {tile_z:3d}): lat={final_lat_offset:+.6f}, lon={final_lon_offset:+.6f} (from {len(contributing_neighbors)} neighbors)")

        # If no corrections made this pass, we're done
        if not newly_corrected:
            print("  No new corrections found")
            break

        # Add newly corrected tiles to corrections dict
        corrections.update(newly_corrected)

        # Apply corrections to bounds and add to virtually_trusted
        for tile_coords, correction in newly_corrected.items():
            original_bounds = remaining_to_correct[tile_coords]
            corrected_bounds = {
                'lon_east': original_bounds['lon_east'] + correction['lon_offset'],
                'lon_west': original_bounds['lon_west'] + correction['lon_offset'],
                'lat_north': original_bounds['lat_north'] + correction['lat_offset'],
                'lat_south': original_bounds['lat_south'] + correction['lat_offset'],
                'exists': True
            }
            virtually_trusted[tile_coords] = corrected_bounds

        # Remove corrected tiles from remaining_to_correct
        for tile_coords in newly_corrected.keys():
            del remaining_to_correct[tile_coords]

        # Re-evaluate uncategorized tiles if they exist (only for scan-range)
        if 'still_uncategorized' in locals():
            newly_categorized_trusted = {}
            newly_categorized_correction = {}

            for tile_coords, tile_bounds in list(still_uncategorized.items()):
                tile_x, tile_z = tile_coords

                # Check alignment with all 4 neighbors
                is_aligned = False
                max_gap = 0.0
                has_trusted_neighbor = False

                for (nx, nz), edge in get_neighbors(tile_x, tile_z):
                    # Check against virtually_trusted neighbors
                    if (nx, nz) not in virtually_trusted:
                        continue

                    has_trusted_neighbor = True
                    neighbor_bounds = virtually_trusted[(nx, nz)]
                    gap = calculate_gap(tile_bounds, neighbor_bounds, edge)
                    abs_gap = abs(gap)
                    max_gap = max(max_gap, abs_gap)

                    if abs_gap < threshold:
                        # Aligned with this trusted neighbor
                        is_aligned = True
                        break

                if is_aligned:
                    newly_categorized_trusted[tile_coords] = tile_bounds
                    virtually_trusted[tile_coords] = tile_bounds
                elif has_trusted_neighbor:
                    # Has trusted neighbors but not aligned
                    newly_categorized_correction[tile_coords] = tile_bounds
                    remaining_to_correct[tile_coords] = tile_bounds

            # Remove newly categorized tiles from still_uncategorized
            for tile_coords in newly_categorized_trusted:
                del still_uncategorized[tile_coords]
            for tile_coords in newly_categorized_correction:
                del still_uncategorized[tile_coords]

            if newly_categorized_trusted or newly_categorized_correction:
                print(f"  Re-categorized {len(newly_categorized_trusted)} as trusted, {len(newly_categorized_correction)} as needing correction")

        print("-"*80)
        print(f"Pass {pass_num} complete: {len(newly_corrected)} corrections added")
        print(f"Remaining tiles to correct: {len(remaining_to_correct)}")
        if 'still_uncategorized' in locals() and still_uncategorized:
            print(f"Uncategorized: {len(still_uncategorized)}")
        print()

        pass_num += 1

    # Compile statistics
    statistics = {
        'total_tiles_loaded': total_tiles_loaded,
        'tiles_checked': tiles_checked,
        'trusted_tiles': len(trusted),
        'corrections_added': corrections_added,
        'remaining_to_correct': len(remaining_to_correct),
        'still_uncategorized': len(still_uncategorized),
        'passes_completed': pass_num - 2,
        'max_passes': max_passes
    }

    return (corrections, statistics)

def cmd_scan_range(args):
    """Scan a rectangular tile range for misalignments"""
    print("="*80)
    print("SCANNING TILE RANGE FOR MISALIGNMENTS")
    print("="*80)
    print()

    x_min, x_max = args.x_min, args.x_max
    z_min, z_max = args.z_min, args.z_max

    # Validate range
    if x_min > x_max:
        print(f"ERROR: x_min ({x_min}) must be <= x_max ({x_max})")
        return 1
    if z_min > z_max:
        print(f"ERROR: z_min ({z_min}) must be <= z_max ({z_max})")
        return 1

    print(f"Tile range: X=[{x_min}, {x_max}], Z=[{z_min}, {z_max}]")
    range_size = (x_max - x_min + 1) * (z_max - z_min + 1)
    print(f"Total tiles in range: {range_size}")
    print()

    # Determine home tile
    if args.home_tile:
        try:
            home_x, home_z = map(int, args.home_tile.split(','))
        except:
            print(f"ERROR: Invalid --home-tile format. Use: x,z (e.g., -7,42)")
            return 1

        if not (x_min <= home_x <= x_max and z_min <= home_z <= z_max):
            print(f"ERROR: Home tile ({home_x}, {home_z}) is outside the specified range")
            return 1

        home_tile = (home_x, home_z)
        print(f"Home tile (specified): {home_tile}")
    else:
        # Find first valid tile in range
        home_tile = None
        print("Finding first valid tile in range as home tile...")
        for z in range(z_min, z_max + 1):
            for x in range(x_min, x_max + 1):
                bounds = load_tile_bounds_from_file(x, z)
                if bounds is not None:
                    home_tile = (x, z)
                    print(f"Home tile (auto-detected): {home_tile}")
                    break
            if home_tile:
                break

        if home_tile is None:
            print(f"ERROR: No valid tiles found in range")
            return 1

    print()

    # Call core scanning logic
    try:
        corrections, stats = _scan_tile_range_core(
            x_min, x_max, z_min, z_max, home_tile,
            args.threshold, args.max_passes, args.output
        )
    except ValueError as e:
        print(f"ERROR: {e}")
        return 1

    # Print final summary
    if stats['passes_completed'] > stats['max_passes']:
        print(f"WARNING: Reached maximum passes ({stats['max_passes']}). {stats['remaining_to_correct']} tiles still need correction.")
        print()

    print("-"*80)
    print()
    print(f"Scan complete:")
    print(f"  Tiles in range: {stats['total_tiles_loaded']}")
    print(f"  Trusted tiles: {stats['trusted_tiles']}")
    print(f"  Corrections added: {stats['corrections_added']}")
    print(f"  Total corrections: {len(corrections)}")

    if stats['remaining_to_correct'] > 0:
        print(f"  WARNING: {stats['remaining_to_correct']} tiles still need correction")
        print(f"           Consider increasing --max-passes (current: {stats['max_passes']})")

    if stats['still_uncategorized'] > 0:
        print(f"  WARNING: {stats['still_uncategorized']} tiles remain uncategorized")
        print(f"           These tiles have no path to the home tile through aligned tiles")

    if stats['corrections_added'] > 0:
        save_corrections_csv(corrections, args.output)
        print(f"\nSaved to: {args.output}")

    return 0

def cmd_scan_all(args):
    """Auto-discover and scan all tiles in directory for misalignments"""
    print("="*80)
    print("AUTO-DISCOVERING ALL TILES")
    print("="*80)
    print()

    # Discover tiles from directory
    tile_dir = getattr(args, 'tile_dir', TILE_DIR)
    try:
        tile_coords, (x_min, x_max), (z_min, z_max) = discover_tiles_from_directory(tile_dir)
    except IOError as e:
        print(f"ERROR: {e}")
        return 1

    # Display discovery summary
    tile_count = len(tile_coords)
    grid_width = x_max - x_min + 1
    grid_height = z_max - z_min + 1
    grid_area = grid_width * grid_height
    coverage_pct = (tile_count / grid_area * 100) if grid_area > 0 else 0

    print(f"Discovered {tile_count:,} tiles")
    print()
    print("Tile coverage:")
    print(f"  X range: [{x_min}, {x_max}] ({grid_width} columns)")
    print(f"  Z range: [{z_min}, {z_max}] ({grid_height} rows)")
    print(f"  Total grid area: {grid_area:,} positions")
    print(f"  Actual tiles: {tile_count:,} ({coverage_pct:.1f}% coverage)")
    print()

    # Parse and validate home tile
    if not args.home_tile:
        print("ERROR: --home-tile is required for scan-all")
        print("       Specify a known good tile as starting point")
        print(f"       Example: --home-tile -7,42")
        return 1

    try:
        home_x, home_z = map(int, args.home_tile.split(','))
    except:
        print(f"ERROR: Invalid --home-tile format. Use: x,z (e.g., -7,42)")
        return 1

    home_tile = (home_x, home_z)

    # Validate home tile exists in discovered tiles
    if home_tile not in tile_coords:
        print(f"ERROR: Home tile ({home_x}, {home_z}) not found in discovered tiles")
        print(f"       Make sure the tile exists in: {tile_dir}")
        return 1

    print(f"Home tile (specified): {home_tile}")
    print(f"Threshold: {args.threshold}°")
    print(f"Max passes: {args.max_passes}")
    print()

    # Warning for large datasets
    if tile_count > 5000 and not args.force:
        est_minutes = (tile_count * 0.05) / 60  # ~50ms per tile
        print(f"WARNING: This will load {tile_count:,} tiles")
        print(f"         Estimated time for Pass 1: ~{est_minutes:.1f} minutes")
        print(f"         Consider using 'scan-range' for smaller regions")
        print(f"         Use --force to proceed anyway")
        print()
        return 1

    # Call core scanning logic
    try:
        corrections, stats = _scan_tile_range_core(
            x_min, x_max, z_min, z_max, home_tile,
            args.threshold, args.max_passes, args.output, tile_dir
        )
    except ValueError as e:
        print(f"ERROR: {e}")
        return 1

    # Print final summary
    if stats['passes_completed'] > stats['max_passes']:
        print(f"WARNING: Reached maximum passes ({stats['max_passes']}). {stats['remaining_to_correct']} tiles still need correction.")
        print()

    print("-"*80)
    print()
    print(f"Scan complete:")
    print(f"  Tiles in range: {stats['total_tiles_loaded']:,}")
    print(f"  Trusted tiles: {stats['trusted_tiles']:,}")
    print(f"  Corrections added: {stats['corrections_added']:,}")
    print(f"  Passes completed: {stats['passes_completed']}")

    if stats['remaining_to_correct'] > 0:
        print(f"  WARNING: {stats['remaining_to_correct']:,} tiles still need correction")
        print(f"           Consider increasing --max-passes (current: {stats['max_passes']})")

    if stats['still_uncategorized'] > 0:
        print(f"  WARNING: {stats['still_uncategorized']:,} tiles remain uncategorized")
        print(f"           These tiles have no path to home tile through aligned tiles")

    # Save corrections
    if stats['corrections_added'] > 0:
        save_corrections_csv(corrections, args.output)
        print(f"\nSaved to: {args.output}")
    else:
        print(f"\nNo corrections needed - all tiles properly aligned!")

    return 0

# ============================================================================
# MAIN
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description='Manage tile corrections')
    subparsers = parser.add_subparsers(dest='command', help='Command to run')

    # Inspect command
    inspect_parser = subparsers.add_parser('inspect', help='Inspect a tile and its neighbors')
    inspect_parser.add_argument('tile_x', type=int, help='Tile X coordinate')
    inspect_parser.add_argument('tile_z', type=int, help='Tile Z coordinate')

    # Add command
    add_parser = subparsers.add_parser('add', help='Add a correction')
    add_parser.add_argument('tile_x', type=int, help='Tile X coordinate')
    add_parser.add_argument('tile_z', type=int, help='Tile Z coordinate')
    add_parser.add_argument('--lat-offset', type=float, help='Latitude offset to add')
    add_parser.add_argument('--lon-offset', type=float, help='Longitude offset to add')
    add_parser.add_argument('--align-to', help='Reference tile to align to (format: x,z)')
    add_parser.add_argument('--edge', choices=['north', 'south', 'east', 'west'], help='Which edge to align')
    add_parser.add_argument('--notes', help='Notes about this correction')
    add_parser.add_argument('--output', default=DEFAULT_CSV, help=f'Output CSV file (default: {DEFAULT_CSV})')
    add_parser.add_argument('--force', action='store_true', help='Overwrite existing correction')

    # Scan command
    scan_parser = subparsers.add_parser('scan', help='Scan for misaligned tiles')
    scan_parser.add_argument('--track-db', required=True, help='Path to track database file')
    scan_parser.add_argument('--start-section', type=int, required=True, help='Starting section ID')
    scan_parser.add_argument('--threshold', type=float, default=DEFAULT_THRESHOLD,
                            help=f'Gap threshold in degrees (default: {DEFAULT_THRESHOLD})')
    scan_parser.add_argument('--max-passes', type=int, default=20,
                            help='Maximum number of correction passes (default: 20)')
    scan_parser.add_argument('--output', default=DEFAULT_CSV, help=f'Output CSV file (default: {DEFAULT_CSV})')

    # Scan-range command
    scan_range_parser = subparsers.add_parser('scan-range', help='Scan a specific tile range for misalignments')
    scan_range_parser.add_argument('x_min', type=int, help='Minimum X coordinate')
    scan_range_parser.add_argument('x_max', type=int, help='Maximum X coordinate')
    scan_range_parser.add_argument('z_min', type=int, help='Minimum Z coordinate')
    scan_range_parser.add_argument('z_max', type=int, help='Maximum Z coordinate')
    scan_range_parser.add_argument('--home-tile', help='Home tile coordinates (format: x,z). If not specified, first valid tile in range is used.')
    scan_range_parser.add_argument('--threshold', type=float, default=DEFAULT_THRESHOLD,
                                   help=f'Gap threshold in degrees (default: {DEFAULT_THRESHOLD})')
    scan_range_parser.add_argument('--max-passes', type=int, default=20,
                                   help='Maximum number of correction passes (default: 20)')
    scan_range_parser.add_argument('--output', default=DEFAULT_CSV, help=f'Output CSV file (default: {DEFAULT_CSV})')

    # Scan-all command
    scan_all_parser = subparsers.add_parser('scan-all',
        help='Auto-discover and scan all tiles in directory')
    scan_all_parser.add_argument('--home-tile', required=True,
        help='Home tile coordinates (format: x,z). REQUIRED - specify a known good tile.')
    scan_all_parser.add_argument('--threshold', type=float, default=DEFAULT_THRESHOLD,
        help=f'Gap threshold in degrees (default: {DEFAULT_THRESHOLD})')
    scan_all_parser.add_argument('--max-passes', type=int, default=30,
        help='Maximum number of correction passes (default: 30)')
    scan_all_parser.add_argument('--output', default=DEFAULT_CSV,
        help=f'Output CSV file (default: {DEFAULT_CSV})')
    scan_all_parser.add_argument('--force', action='store_true',
        help='Proceed even with large tile counts (>5000 tiles)')
    scan_all_parser.add_argument('--tile-dir', default=TILE_DIR,
        help=f'Tile directory path (default: {TILE_DIR})')

    args = parser.parse_args()

    if args.command == 'inspect':
        return cmd_inspect(args)
    elif args.command == 'add':
        return cmd_add(args)
    elif args.command == 'scan':
        return cmd_scan(args)
    elif args.command == 'scan-range':
        return cmd_scan_range(args)
    elif args.command == 'scan-all':
        return cmd_scan_all(args)
    else:
        parser.print_help()
        return 1

if __name__ == '__main__':
    sys.exit(main())
