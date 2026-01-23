#!/usr/bin/env python3
"""
Reliable TR4 Tile Coordinate Parser
Based on analysis of multiple tile files, this parser handles two main patterns:
1. Sentinel-based: Files with uint32 sentinel value 3, coordinates at +40 bytes offset
2. Direct access: Files where coordinates appear directly without sentinel pattern

Key findings:
- Sentinel-based files: coordinates at fixed +40 bytes from sentinel value 3
- Direct access files: coordinates typically followed by "NoCalVeg01" or similar strings
- All coordinates are stored as 4 consecutive floats (little-endian): east, west, north, south
- Tile sizes are consistently ~0.009° × 0.009° (high-resolution tiles)
"""

import struct
import zlib
import sys
import os

# Geographic bounds for validation (USA)
LON_MIN, LON_MAX = -180.0, 0.0
LAT_MIN, LAT_MAX = 0.0, 90.0

def decompress_tr4(filepath):
    """Decompress a TR4 file using raw DEFLATE"""
    with open(filepath, 'rb') as f:
        compressed_data = f.read()
    
    try:
        decompressed = zlib.decompress(compressed_data, -zlib.MAX_WBITS)
        return decompressed
    except Exception as e:
        print(f"ERROR: Failed to decompress {filepath}: {e}")
        return None

def extract_coords_at_offset(data, offset):
    """Extract and validate coordinates at a specific offset"""
    try:
        lon_east = struct.unpack('<f', data[offset:offset+4])[0]
        lon_west = struct.unpack('<f', data[offset+4:offset+8])[0]
        lat_north = struct.unpack('<f', data[offset+8:offset+12])[0]
        lat_south = struct.unpack('<f', data[offset+12:offset+16])[0]
        
        # Validate coordinates - stricter validation to reject false positives
        if (LON_MIN < lon_east < LON_MAX and LON_MIN < lon_west < LON_MAX and
            LAT_MIN < lat_north < LAT_MAX and LAT_MIN < lat_south < LAT_MAX and
            lon_west < lon_east and lat_south < lat_north and
            0.005 < abs(lon_east - lon_west) < 0.02 and 
            0.005 < abs(lat_north - lat_south) < 0.02 and
            # Additional checks to reject obvious false positives
            abs(lon_east) < 180 and abs(lon_west) < 180 and  # Reasonable longitude range
            abs(lat_north) < 90 and abs(lat_south) < 90 and      # Reasonable latitude range
            not (abs(lon_east) > 1000 or abs(lon_west) > 1000 or  # Reject huge values
                abs(lat_north) > 1000 or abs(lat_south) > 1000) and  # Reject huge values
            not (abs(lon_east - lon_west) > 1.0 or          # Reject overly large tiles
                abs(lat_north - lat_south) > 1.0)):        # Reject overly large tiles
            
            return {
                'lon_east': lon_east,
                'lon_west': lon_west,
                'lat_north': lat_north,
                'lat_south': lat_south,
                'width': abs(lon_east - lon_west),
                'height': abs(lat_north - lat_south)
            }
    except:
        pass
    
    return None

def parse_with_sentinel(data):
    """Try sentinel-based parsing (pattern 1)"""
    for offset in range(0, len(data) - 44):
        try:
            sentinel = struct.unpack('<I', data[offset:offset+4])[0]
            if sentinel == 3:
                coord_offset = offset + 40
                coords = extract_coords_at_offset(data, coord_offset)
                if coords:
                    return {
                        'coords': coords,
                        'method': 'sentinel',
                        'sentinel_offset': offset,
                        'coord_offset': coord_offset
                    }
        except:
            pass
    
    return None

def parse_direct_access(data):
    """Try direct access parsing (pattern 2)"""
    # Look for coordinates followed by known strings
    known_strings = [b'NoCalVeg01', b'NSF_Mojave', b'NSF_Sierra', b'ProcVeg01']
    
    # First pass: look for coordinates with following strings
    for offset in range(0, len(data) - 16):
        coords = extract_coords_at_offset(data, offset)
        if coords:
            # Check if followed by known string pattern
            for i in range(16, min(64, len(data) - offset)):
                chunk = data[offset + 16:offset + 16 + i]
                for known_str in known_strings:
                    if known_str in chunk:
                        return {
                            'coords': coords,
                            'method': 'direct_with_string',
                            'coord_offset': offset,
                            'following_string': known_str.decode('ascii', errors='ignore')
                        }
    
    # Second pass: find ALL valid coordinates and choose the best one
    valid_coords = []
    for offset in range(0, len(data) - 16):
        coords = extract_coords_at_offset(data, offset)
        if coords:
            valid_coords.append({
                'coords': coords,
                'coord_offset': offset
            })
    
    # Choose the best coordinates based on geographic plausibility
    if valid_coords:
        # Prefer coordinates that look like real California tiles
        best_score = -1
        best_coords = None
        
        for coord_set in valid_coords:
            coords = coord_set['coords']
            score = 0
            
            # Prefer realistic latitude/longitude ranges
            if -130 <= coords['lon_east'] <= -110:  # California longitude range
                score += 10
            if 30 <= coords['lat_north'] <= 40:     # California latitude range  
                score += 10
                
            # Prefer reasonable tile sizes
            tile_width = abs(coords['lon_east'] - coords['lon_west'])
            tile_height = abs(coords['lat_north'] - coords['lat_south'])
            if 0.005 <= tile_width <= 0.015 and 0.005 <= tile_height <= 0.015:
                score += 5
                
            # Avoid coordinates near zero (likely false positives)
            if abs(coords['lon_east']) < 1 and abs(coords['lat_north']) < 1:
                score -= 20
                
            if score > best_score:
                best_score = score
                best_coords = coord_set
        
        if best_coords:
            best_coords['method'] = 'direct_best_coordinates'
            return best_coords
    
    return None

def parse_tr4_coordinates(filepath):
    """Main parsing function - tries all patterns"""
    data = decompress_tr4(filepath)
    if not data:
        return None
    
    # Try sentinel-based method first
    result = parse_with_sentinel(data)
    if result:
        return result
    
    # Fall back to direct access
    result = parse_direct_access(data)
    if result:
        return result
    
    return None

def format_result(filepath, result):
    """Format parsing result for display"""
    coords = result['coords']
    print(f"File: {os.path.basename(filepath)}")
    print(f"Method: {result['method']}")
    
    if 'sentinel_offset' in result:
        print(f"Sentinel at: {result['sentinel_offset']} (0x{result['sentinel_offset']:08X})")
        print(f"Coordinates at: {result['coord_offset']} (0x{result['coord_offset']:08X})")
        print(f"Offset from sentinel: {result['coord_offset'] - result['sentinel_offset']} bytes")
    else:
        print(f"Coordinates at: {result['coord_offset']} (0x{result['coord_offset']:08X})")
        if 'following_string' in result:
            print(f"Following string: {result['following_string']}")
    
    print(f"Bounds:")
    print(f"  East:  {coords['lon_east']:11.6f}°")
    print(f"  West:  {coords['lon_west']:11.6f}°")
    print(f"  North: {coords['lat_north']:11.6f}°")
    print(f"  South: {coords['lat_south']:11.6f}°")
    print(f"Size:")
    print(f"  Width:  {coords['width']:.6f}°")
    print(f"  Height: {coords['height']:.6f}°")
    print()

def main():
    if len(sys.argv) < 2:
        print("Usage: python reliable_parser.py <tr4_file> [tr4_file2 ...]")
        print("Example: python reliable_parser.py *.tr4")
        return 1
    
    success_count = 0
    total_count = len(sys.argv) - 1
    
    for filepath in sys.argv[1:]:
        if not os.path.exists(filepath):
            print(f"ERROR: File not found: {filepath}")
            continue
        
        result = parse_tr4_coordinates(filepath)
        if result:
            format_result(filepath, result)
            success_count += 1
        else:
            print(f"FAILED: Could not parse {filepath}")
            print()
    
    print("="*60)
    print(f"SUMMARY: Successfully parsed {success_count}/{total_count} files")
    
    return 0 if success_count > 0 else 1

if __name__ == '__main__':
    sys.exit(main())