#!/usr/bin/env python3
"""
Output generator for Run8 Track Mapper.

Generates manifest.json and per-region JSON files from extracted region data.
"""

import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Dict, List, Tuple

from config_parser import VisualizationConfig, parse_config
from region_extractor import (
    RegionData, SectionData, SignalData, AILocationData, IndustryData, TileData,
    extract_region, load_tile_corrections
)


def section_to_dict(section: SectionData) -> dict:
    """Convert SectionData to JSON-serializable dict"""
    return {
        "id": section.id,
        "paths": section.paths,  # List of paths, each path is list of (lat, lon) tuples
        "length_ft": section.length_ft,
        "length_m": section.length_m,
        "is_switch": section.is_switch
    }


def signal_to_dict(signal: SignalData) -> dict:
    """Convert SignalData to JSON-serializable dict"""
    return {
        "id": signal.id,
        "lat": signal.lat,
        "lon": signal.lon,
        "rotation": signal.rotation,
        "type": signal.signal_type,
        "name": signal.name,
        "model_name": signal.model_name,
        "is_dwarf": signal.is_dwarf,
        "is_switch_indicator": signal.is_switch_indicator,
        "is_advance_diverging": signal.is_advance_diverging
    }


def ai_location_to_dict(loc: AILocationData) -> dict:
    """Convert AILocationData to JSON-serializable dict"""
    return {
        "id": loc.id,
        "name": loc.name,
        "lat": loc.lat,
        "lon": loc.lon,
        "type_id": loc.type_id,
        "type_name": loc.type_name
    }


def industry_to_dict(ind: IndustryData) -> dict:
    """Convert IndustryData to JSON-serializable dict"""
    return {
        "tag": ind.tag,
        "name": ind.name,
        "lat": ind.lat,
        "lon": ind.lon,
        "track_sections": ind.track_sections
    }


def tile_to_dict(tile: TileData) -> dict:
    """Convert TileData to JSON-serializable dict"""
    return {
        "x": tile.x,
        "z": tile.z,
        "lat_north": tile.lat_north,
        "lat_south": tile.lat_south,
        "lon_east": tile.lon_east,
        "lon_west": tile.lon_west,
        "is_corrected": tile.is_corrected
    }


def region_to_dict(region: RegionData) -> dict:
    """Convert RegionData to JSON-serializable dict for region file"""
    return {
        "id": region.id,
        "display_name": region.display_name,
        "sections": [section_to_dict(s) for s in region.sections],
        "signals": [signal_to_dict(s) for s in region.signals],
        "ai_locations": [ai_location_to_dict(a) for a in region.ai_locations],
        "industries": [industry_to_dict(i) for i in region.industries],
        "tiles": [tile_to_dict(t) for t in region.tiles]
    }


def generate_manifest(config: VisualizationConfig,
                      regions_data: List[RegionData],
                      tile_corrections: Dict[Tuple[int, int], Dict[str, float]]) -> dict:
    """Generate manifest.json content"""
    regions_manifest = []

    for region in regions_data:
        # Find the config for this region to get enabled_by_default
        region_config = next(r for r in config.regions if r.id == region.id)

        region_entry = {
            "id": region.id,
            "display_name": region.display_name,
            "enabled_by_default": region_config.enabled_by_default,
        }

        # Add bounds if available
        if region.bounds:
            region_entry["bounds"] = [
                [region.bounds[0][0], region.bounds[0][1]],  # [min_lat, min_lon]
                [region.bounds[1][0], region.bounds[1][1]]   # [max_lat, max_lon]
            ]

        regions_manifest.append(region_entry)

    # Convert tile corrections to serializable format
    tile_corrections_list = [
        {
            "tile_x": k[0],
            "tile_z": k[1],
            "lat_offset": v["lat_offset"],
            "lon_offset": v["lon_offset"]
        }
        for k, v in tile_corrections.items()
    ]

    return {
        "name": config.name,
        "regions": regions_manifest,
        "tile_corrections": tile_corrections_list
    }


def generate_output(config: VisualizationConfig, tile_dir: str = None, generate_html: bool = True) -> None:
    """Generate all output files (manifest.json, per-region JSON files, and index.html)"""
    output_dir = config.output_dir
    data_dir = output_dir / "data"

    # Create output directories
    output_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    print(f"\nOutput directory: {output_dir}")
    print(f"Data directory: {data_dir}")

    # Load tile corrections
    print(f"\nLoading tile corrections from: {config.tile_corrections}")
    tile_corrections = load_tile_corrections(str(config.tile_corrections))

    # Extract all regions
    regions_data = []
    for region_config in config.regions:
        kwargs = {"tile_dir": tile_dir} if tile_dir else {}
        region_data = extract_region(
            region_config,
            config.industry_db,
            tile_corrections,
            **kwargs
        )
        regions_data.append(region_data)

        # Write region JSON file
        region_file = data_dir / f"{region_config.id}.json"
        print(f"\nWriting region file: {region_file}")

        region_dict = region_to_dict(region_data)
        with open(region_file, 'w', encoding='utf-8') as f:
            json.dump(region_dict, f, indent=2)

        # Report file size
        file_size = region_file.stat().st_size
        if file_size > 1024 * 1024:
            print(f"  Size: {file_size / (1024*1024):.2f} MB")
        else:
            print(f"  Size: {file_size / 1024:.1f} KB")

    # Generate and write manifest
    manifest_file = output_dir / "manifest.json"
    print(f"\nWriting manifest: {manifest_file}")

    manifest = generate_manifest(config, regions_data, tile_corrections)
    with open(manifest_file, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=2)

    # Generate HTML if requested
    if generate_html:
        from html_generator import generate_html as gen_html
        html_file = output_dir / "index.html"
        print(f"\nGenerating HTML: {html_file}")
        gen_html(config, html_file)

    print(f"\nOutput generation complete!")
    print(f"  Manifest: {manifest_file}")
    if generate_html:
        print(f"  HTML: {output_dir / 'index.html'}")
    print(f"  Regions: {len(regions_data)}")
    for region in regions_data:
        print(f"    - {region.id}: {len(region.sections)} sections, {len(region.signals)} signals")


if __name__ == '__main__':
    import sys

    if len(sys.argv) < 2:
        print("Usage: output_generator.py <config.ini>")
        sys.exit(1)

    config = parse_config(sys.argv[1])
    generate_output(config)
