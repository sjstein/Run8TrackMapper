#!/usr/bin/env python
"""
Tile Explorer Tool - Analyze TR4 tile files to find geographic bounds

This tool helps understand the structure of TR4 tile files by:
1. Decompressing the file
2. Searching for sentinel value (uint32 = 3)
3. If sentinel found: showing bounds and file positions
4. If no sentinel: searching for plausible lat/lon coordinates within USA

Usage:
  python explore_tile.py <tile_file.tr4>

Example:
  python explore_tile.py "C:\\Run8Studios\\...\\00001_00001.tr4"
"""

import struct
import zlib
import sys
import os

# Geographic bounds for USA (with margins)
LON_MIN = -130.0  # West coast (with margin)
LON_MAX = -65.0   # East coast (with margin)
LAT_MIN = 23.0    # Southern border (with margin)
LAT_MAX = 50.0    # Northern border (with margin)

def decompress_tr4(filepath):
    """Decompress a TR4 file using raw DEFLATE"""
    with open(filepath, 'rb') as f:
        compressed_data = f.read()

    print(f"Compressed file size: {len(compressed_data):,} bytes")

    try:
        decompressed = zlib.decompress(compressed_data, -zlib.MAX_WBITS)
        print(f"Decompressed size: {len(decompressed):,} bytes")
        print(f"Compression ratio: {len(compressed_data) / len(decompressed):.1%}")
        return decompressed
    except Exception as e:
        print(f"ERROR: Failed to decompress: {e}")
        return None

def search_sentinel3(data):
    """Search for sentinel value 3 and extract coordinates"""
    print("\n" + "="*80)
    print("SEARCHING FOR SENTINEL VALUE 3")
    print("="*80)

    found_sentinels = []

    for offset in range(0, len(data) - 44):
        try:
            sentinel = struct.unpack('<I', data[offset:offset+4])[0]
            if sentinel == 3:
                # Coordinates are 40 bytes after sentinel
                coord_offset = offset + 40

                if coord_offset + 16 <= len(data):
                    lon_east = struct.unpack('<f', data[coord_offset:coord_offset+4])[0]
                    lon_west = struct.unpack('<f', data[coord_offset+4:coord_offset+8])[0]
                    lat_north = struct.unpack('<f', data[coord_offset+8:coord_offset+12])[0]
                    lat_south = struct.unpack('<f', data[coord_offset+12:coord_offset+16])[0]

                    # Check if coordinates look valid
                    is_valid = (
                        -180 < lon_east < 0 and -180 < lon_west < 0 and
                        0 < lat_north < 90 and 0 < lat_south < 90 and
                        abs(lon_east - lon_west) < 1 and abs(lat_north - lat_south) < 1 and
                        lat_south < lat_north and lon_west < lon_east
                    )

                    found_sentinels.append({
                        'offset': offset,
                        'coord_offset': coord_offset,
                        'lon_east': lon_east,
                        'lon_west': lon_west,
                        'lat_north': lat_north,
                        'lat_south': lat_south,
                        'valid': is_valid
                    })
        except:
            pass

    return found_sentinels

def search_plausible_coords(data, max_results=50):
    """Search for plausible lat/lon coordinates within USA bounds"""
    print("\n" + "="*80)
    print("SEARCHING FOR PLAUSIBLE COORDINATES (NO SENTINEL)")
    print("="*80)
    print(f"Looking for coordinates within USA bounds:")
    print(f"  Longitude: {LON_MIN} to {LON_MAX}")
    print(f"  Latitude: {LAT_MIN} to {LAT_MAX}")
    print(f"  Max tile size: 0.05 degrees")
    print()

    found_coords = []

    for offset in range(0, len(data) - 16):
        try:
            lon_east = struct.unpack('<f', data[offset:offset+4])[0]
            lon_west = struct.unpack('<f', data[offset+4:offset+8])[0]
            lat_north = struct.unpack('<f', data[offset+8:offset+12])[0]
            lat_south = struct.unpack('<f', data[offset+12:offset+16])[0]

            # Validate coordinates are within USA bounds
            if (LON_MIN < lon_east < LON_MAX and LON_MIN < lon_west < LON_MAX and
                LAT_MIN < lat_north < LAT_MAX and LAT_MIN < lat_south < LAT_MAX and
                abs(lon_east - lon_west) < 0.05 and abs(lat_north - lat_south) < 0.05 and
                lon_west < lon_east and lat_south < lat_north):

                found_coords.append({
                    'offset': offset,
                    'lon_east': lon_east,
                    'lon_west': lon_west,
                    'lat_north': lat_north,
                    'lat_south': lat_south
                })

                # Limit results to avoid overwhelming output
                if len(found_coords) >= max_results:
                    break
        except:
            pass

    return found_coords

def display_sentinel_results(sentinels):
    """Display sentinel search results"""
    if not sentinels:
        print("\nNO SENTINEL VALUES FOUND")
        return False

    valid_sentinels = [s for s in sentinels if s['valid']]
    invalid_sentinels = [s for s in sentinels if not s['valid']]

    print(f"\nFound {len(sentinels)} sentinel value(s) total")
    print(f"  Valid:   {len(valid_sentinels)}")
    print(f"  Invalid: {len(invalid_sentinels)}")
    print()

    if valid_sentinels:
        print("="*80)
        print("VALID SENTINELS")
        print("="*80)
        for i, s in enumerate(valid_sentinels, 1):
            print(f"\nValid Sentinel #{i}:")
            print(f"  Sentinel offset:     {s['offset']:,} (0x{s['offset']:08X})")
            print(f"  Coordinate offset:   {s['coord_offset']:,} (0x{s['coord_offset']:08X})")
            print(f"  Bounds:")
            print(f"    East:  {s['lon_east']:11.6f}")
            print(f"    West:  {s['lon_west']:11.6f}")
            print(f"    North: {s['lat_north']:11.6f}")
            print(f"    South: {s['lat_south']:11.6f}")
            print(f"  Size:")
            print(f"    Width:  {abs(s['lon_east'] - s['lon_west']):.6f} degrees")
            print(f"    Height: {abs(s['lat_north'] - s['lat_south']):.6f} degrees")

    if invalid_sentinels and len(invalid_sentinels) <= 10:
        print()
        print("="*80)
        print(f"INVALID SENTINELS (showing all {len(invalid_sentinels)})")
        print("="*80)
        for i, s in enumerate(invalid_sentinels, 1):
            print(f"\nInvalid Sentinel #{i}:")
            print(f"  Sentinel offset:     {s['offset']:,} (0x{s['offset']:08X})")
            print(f"  Coordinate offset:   {s['coord_offset']:,} (0x{s['coord_offset']:08X})")
            print(f"  Values: E={s['lon_east']:.2f}, W={s['lon_west']:.2f}, N={s['lat_north']:.2f}, S={s['lat_south']:.2f}")
    elif invalid_sentinels:
        print()
        print(f"(Not showing {len(invalid_sentinels)} invalid sentinels - too many false positives)")

    print()
    return len(valid_sentinels) > 0

def display_coordinate_results(coords):
    """Display coordinate search results"""
    if not coords:
        print("\nNO PLAUSIBLE COORDINATES FOUND")
        return False

    print(f"\nFound {len(coords)} plausible coordinate set(s)")
    print()

    for i, c in enumerate(coords, 1):
        print(f"Coordinate Set #{i}:")
        print(f"  File offset:     {c['offset']:,} (0x{c['offset']:08X})")
        print(f"  Bounds:")
        print(f"    East:  {c['lon_east']:11.6f}")
        print(f"    West:  {c['lon_west']:11.6f}")
        print(f"    North: {c['lat_north']:11.6f}")
        print(f"    South: {c['lat_south']:11.6f}")
        print(f"  Size:")
        print(f"    Width:  {abs(c['lon_east'] - c['lon_west']):.6f} degrees")
        print(f"    Height: {abs(c['lat_north'] - c['lat_south']):.6f} degrees")
        print()

    return True

def explore_tile(filepath):
    """Main exploration function"""
    print("="*80)
    print("TILE EXPLORER")
    print("="*80)
    print(f"File: {os.path.basename(filepath)}")
    print(f"Path: {filepath}")
    print()

    if not os.path.exists(filepath):
        print(f"ERROR: File not found: {filepath}")
        return 1

    # Decompress
    data = decompress_tr4(filepath)
    if data is None:
        return 1

    with open(filepath.split('.')[0]+'.hex', "wb") as fp:
        fp.write(data)

    # Try sentinel method first
    sentinels = search_sentinel3(data)
    found_valid = display_sentinel_results(sentinels)

    # If no valid sentinels, try coordinate search
    if not found_valid:
        print("\n" + "-"*80)
        print("Sentinel method failed, trying coordinate search...")
        print("-"*80)

        coords = search_plausible_coords(data)
        display_coordinate_results(coords)

    print("="*80)
    print("EXPLORATION COMPLETE")
    print("="*80)

    return 0

def main():
    if len(sys.argv) != 2:
        print(__doc__)
        print("\nERROR: Please provide a tile file path")
        print("\nExample:")
        print('  python explore_tile.py "C:\\Run8Studios\\...\\00001_00001.tr4"')
        return 1

    filepath = sys.argv[1]
    return explore_tile(filepath)

if __name__ == '__main__':
    sys.exit(main())
