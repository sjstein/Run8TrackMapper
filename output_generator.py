#!/usr/bin/env python3
"""
Output generator for Run8 Track Mapper.

Generates manifest.json and per-region JSON files from extracted region data.
Can also generate tile reports listing all tiles that tracks pass through.
"""

import argparse
import json
import os
import shutil
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

from config_parser import VisualizationConfig, parse_config
from region_extractor import (
    RegionData, SectionData, SignalData, AILocationData, IndustryData, TileData,
    TrainData, RailVehicleData, extract_region, load_tile_corrections
)


def section_to_dict(section: SectionData) -> dict:
    """Convert SectionData to JSON-serializable dict"""
    return {
        "id": section.id,
        "paths": section.paths,  # List of paths, each path is list of (lat, lon) tuples
        "length_ft": section.length_ft,
        "length_m": section.length_m,
        "is_switch": section.is_switch,
        "track_type": section.track_type,
        "retarder_mph": section.retarder_mph,
        "elevation_start_m": section.elevation_start_m,
        "elevation_end_m": section.elevation_end_m
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
        "is_advance_diverging": signal.is_advance_diverging,
        "stacked_ids": signal.stacked_ids or []
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
        "local_name": ind.local_name,
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


def area_to_dict(area) -> dict:
    """Convert an AreaLabel to a JSON-serializable dict"""
    result = {
        "id": area.id,
        "label": area.label,
        "tile_x": area.tile[0],
        "tile_z": area.tile[1],
        "local_x": area.local[0],
        "local_z": area.local[1],
    }
    if area.color:
        result["color"] = area.color
    if area.font_size:
        result["font_size"] = area.font_size
    if area.box:
        result["box"] = True
    if area.rotation:
        result["rotation"] = area.rotation
    if getattr(area, "type", None):
        result["type"] = area.type
    return result


def rail_vehicle_to_dict(v: RailVehicleData) -> dict:
    """Convert RailVehicleData to JSON-serializable dict"""
    return {
        "train_id": v.train_id,
        "unit_number": v.unit_number,
        "destination_tag": v.destination_tag,
        "unit_type": v.unit_type,
        "rv_filename": v.rv_filename,
        "body": v.body,          # polyline (>=2 pts) from truck A to truck B
        "position_key": v.position_key,
        "resolved": v.resolved,
        "car_type": v.car_type,  # INDUSTRY_CONFIG_CAR_TYPE, for per-type body colour
        "company": v.company,    # loco reporting mark (INITIAL), for per-railroad loco colour
        "front0": v.front0,      # loco facing: True if body[0] is the front end (for the facing arrow)
    }


def train_to_dict(train: TrainData) -> dict:
    """Convert TrainData to JSON-serializable dict"""
    return {
        "train_id": train.train_id,
        "was_ai": train.was_ai,
        "vehicles": [rail_vehicle_to_dict(v) for v in train.vehicles],
        # Whole-consist totals for the hover readout (see TrainData). Tonnage is
        # gross US tons; length is metres (the viewer converts to feet).
        "total_length_m": train.total_length_m,
        "trailing_tons": train.trailing_tons,
        "total_tons": train.total_tons,
        "car_count": train.car_count,
        "veh_count": train.veh_count,
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
        "tiles": [tile_to_dict(t) for t in region.tiles],
        "trains": [train_to_dict(t) for t in region.trains],
    }


def generate_manifest(config: VisualizationConfig,
                      regions_data: List[RegionData],
                      tile_corrections: Dict[Tuple[int, int], Dict[str, float]],
                      tile_based: bool = False,
                      world_totals: dict = None) -> dict:
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

        # Add per-region track color if specified
        if region_config.track_color:
            region_entry["track_color"] = region_config.track_color

        # Add bounds if available
        if region.bounds:
            region_entry["bounds"] = [
                [region.bounds[0][0], region.bounds[0][1]],  # [min_lat/y, min_lon/x]
                [region.bounds[1][0], region.bounds[1][1]]   # [max_lat/y, max_lon/x]
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

    manifest = {
        "name": config.name,
        "regions": regions_manifest,
        "tile_corrections": tile_corrections_list,
        "coordinate_system": "tile_local" if tile_based else "geographic",
        "areas": [area_to_dict(a) for a in config.areas],
        "color_presets": dict(getattr(config, "color_presets", {}) or {}),
        "label_types": [{"id": t.id, "name": t.name, "color": t.color}
                        for t in getattr(config, "label_types", []) or []],
        "car_type_colors": dict(getattr(config, "car_type_colors", {}) or {}),
        "loco_company_colors": dict(getattr(config, "loco_company_colors", {}) or {}),
        "signal_show_intermediate": getattr(config, "signal_show_intermediate", True),
        "initial_map_opacity": getattr(config, "initial_map_opacity", 0.2),
        "initial_track_opacity": getattr(config, "initial_track_opacity", 0.8),
        # Whole-world-save counts (region-independent), for the Trains status line.
        "world_totals": world_totals,
    }

    # Add tile parameters if using tile-based coordinates
    if tile_based and config.tile_based:
        manifest["tile_params"] = {
            "home_tile": list(config.tile_based.home_tile),
            "tile_width": config.tile_based.tile_width,
            "tile_height": config.tile_based.tile_height
        }

    return manifest


def generate_tile_report(config: VisualizationConfig, output_path: str) -> None:
    """Generate a tile report listing all tiles that tracks pass through.

    Args:
        config: Visualization configuration
        output_path: Path to output tile list file
    """
    from explore_trackdb import TrackDatabase

    all_tiles = set()

    print(f"Collecting tiles from {len(config.regions)} region(s)...")

    for region_config in config.regions:
        print(f"  Loading {region_config.display_name}...")
        track_db = TrackDatabase(str(region_config.track_database))

        region_tiles = set()
        for section in track_db.sections:
            for node in section.nodes:
                region_tiles.add(node.tile_index)

        print(f"    Found {len(region_tiles)} tiles")
        all_tiles.update(region_tiles)

    print(f"\nTotal unique tiles: {len(all_tiles)}")

    # Write tile report
    with open(output_path, 'w') as f:
        f.write(f"# Tiles from: {config.name}\n")
        f.write(f"# Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"# Total tiles: {len(all_tiles)}\n")

        for tile_x, tile_z in sorted(all_tiles):
            f.write(f"{tile_x},{tile_z}\n")

    print(f"Tile report saved to: {output_path}")


def compute_align_seed(regions_data, tile_based_config, tile_dir: str):
    """Pick a central track vertex and its real lat/lon (from the tile's .tr4)
    to seed the manual-alignment transform."""
    import math
    from region_extractor import decompress_tr4, find_tile_bounds

    HX, HZ = tile_based_config.home_tile
    TW, TH = tile_based_config.tile_width, tile_based_config.tile_height
    pts = [p for rd in regions_data for sec in rd.sections for path in sec.paths for p in path]
    if not pts:
        return None
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    pts.sort(key=lambda p: (p[0]-cx)**2 + (p[1]-cy)**2)

    def nm(x, z):
        xs = f"{x:06d}" if x < 0 else f"{x:05d}"
        zs = f"{z:06d}" if z < 0 else f"{z:05d}"
        return f"{xs}_{zs}.tr4"

    for X, Y in pts[:5000]:
        tx = HX + math.floor(X/TW); tz = HZ + math.floor(Y/TH)
        fp = os.path.join(tile_dir, nm(tx, tz))
        if not os.path.exists(fp):
            continue
        data = decompress_tr4(fp)
        if not data:
            continue
        b = find_tile_bounds(data)
        if not b:
            continue
        le, lw, ln, ls = b
        lat = ls + ((Y-(tz-HZ)*TH)/TH)*(ln-ls)
        lon = lw + ((X-(tx-HX)*TW)/TW)*(le-lw)
        return {"X": round(float(X), 1), "Y": round(float(Y), 1),
                "lat": lat, "lon": lon, "lat_ref": lat}
    return None


def copy_leaflet_assets(output_dir: Path) -> None:
    """Copy the vendored Leaflet library into the output dir so the standalone
    viewers load it same-origin instead of from the unpkg CDN. This removes an
    external dependency from first load (a source of cold-start flakiness) and
    yields real JS stack traces. The generated HTML references leaflet/leaflet.{js,css}
    and falls back to the CDN at runtime if these files are ever missing."""
    src = Path(__file__).resolve().parent / 'vendor' / 'leaflet'
    if not src.exists():
        print(f"  WARNING: vendored Leaflet not found at {src}; "
              f"the viewer will fall back to the unpkg CDN at runtime")
        return
    dst = output_dir / 'leaflet'
    shutil.copytree(src, dst, dirs_exist_ok=True)
    print(f"  Copied vendored Leaflet -> {dst}")


def generate_output(config: VisualizationConfig, tile_dir: str = None, generate_html: bool = True,
                    authoring: bool = True, world_save: str = None) -> None:
    """Generate all output files (manifest.json, per-region JSON files, and the
    manual-alignment index.html).

    The manual-alignment viewer is the only viewer, so output is always built in
    the tile-coordinate ("uniform grid") mode that viewer aligns onto a real map.

    Args:
        config: Visualization configuration
        tile_dir: Override tile directory
        generate_html: Whether to generate index.html
        authoring: When False, hide the align viewer's "Add Label" button (for hosting)
        world_save: Optional Run8 world save (.xml) to plot trains from; overrides
            config.world_save when given.
    """
    output_dir = config.output_dir
    data_dir = output_dir / "data"

    # Create output directories
    output_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    print(f"\nOutput directory: {output_dir}")
    print(f"Data directory: {data_dir}")

    # The align viewer always uses tile (uniform-grid) coordinates.
    if config.tile_based:
        tile_based_config = config.tile_based
        print(f"\nUsing tile-based coordinates:")
        print(f"  Home tile: {tile_based_config.home_tile}")
        print(f"  Tile size: {tile_based_config.tile_width}m x {tile_based_config.tile_height}m")
    else:
        print("\nWarning: no [tile_based_plot] section in config; using default tile parameters")
        from config_parser import TileBasedConfig
        tile_based_config = TileBasedConfig()

    # Tile corrections are a geographic-viewer concept; the align viewer uses the
    # uniform grid, so none are applied.
    tile_corrections = {}

    # Load an optional world save (CLI --world overrides config world_save).
    world_trains = None
    world_totals = None
    world_sim_time = None
    world_save_path = world_save or (str(config.world_save) if config.world_save else None)
    if world_save_path:
        from world_parser import parse_world_save, parse_sim_time
        print(f"\nLoading world save: {world_save_path}")
        world_trains = parse_world_save(world_save_path)
        n_veh = sum(len(t.vehicles) for t in world_trains)
        # Whole-save totals for the viewer status line (region-independent).
        world_totals = {"trains": len(world_trains), "vehicles": n_veh}
        world_sim_time = parse_sim_time(world_save_path)   # sim clock (<date> tag)
        print(f"  Parsed {len(world_trains)} train(s), {n_veh} rail vehicle(s)")

    # Load the optional rail-vehicle length DB (draws true car length over trucks).
    rv_lengths = None
    if world_trains and config.railvehicle_db:
        from rv_length_db import load_rv_lengths
        if os.path.exists(str(config.railvehicle_db)):
            rv_lengths = load_rv_lengths(str(config.railvehicle_db))
            print(f"  Loaded {len(rv_lengths)} rail-vehicle length(s) from {config.railvehicle_db}")
        else:
            print(f"  Warning: railvehicle_db not found: {config.railvehicle_db}")

    # Extract all regions (trains are placed afterwards, globally across regions)
    regions_data = []
    for region_config in config.regions:
        # tile_dir parameter overrides per-region config if specified
        region_data = extract_region(
            region_config,
            config.industry_db,
            tile_corrections,
            default_tile_dir=str(config.terrain_tile_dir),
            tile_dir=tile_dir,  # None lets each region use its configured terrain_tile_dir
            tile_based_config=tile_based_config
        )
        regions_data.append(region_data)

    # Place trains once against all regions' sections (a car can straddle a region
    # boundary, so each truck must resolve against its own region's geometry).
    if world_trains:
        from region_extractor import build_section_placer, extract_trains
        placer = build_section_placer(
            (rc.route_prefix, rd.sections) for rc, rd in zip(config.regions, regions_data))
        trains_by_prefix = extract_trains(world_trains, placer, rv_lengths=rv_lengths,
                                          deoverlap=getattr(config, 'train_deoverlap', True))
        for rc, rd in zip(config.regions, regions_data):
            rd.trains = trains_by_prefix.get(rc.route_prefix, [])
            if rd.trains:
                n_veh = sum(len(t.vehicles) for t in rd.trains)
                n_res = sum(1 for t in rd.trains for v in t.vehicles if v.resolved)
                print(f"  Placed {n_veh} rail vehicle(s) in {len(rd.trains)} train(s) "
                      f"on {rc.id} (prefix {rc.route_prefix}, {n_res} resolved)")

    # Write region JSON files
    for region_config, region_data in zip(config.regions, regions_data):
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

    manifest = generate_manifest(config, regions_data, tile_corrections, True,
                                 world_totals=world_totals)
    if world_sim_time:
        manifest["world_sim_time"] = world_sim_time
    seed = compute_align_seed(regions_data, tile_based_config, str(config.terrain_tile_dir))
    if seed:
        manifest["align"] = seed
        print(f"  Align seed: {seed['lat']:.5f}, {seed['lon']:.5f}")
    else:
        print("  WARNING: could not compute align seed (no tile .tr4 found)")
    with open(manifest_file, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=2)

    # Generate HTML if requested (always the manual-alignment viewer).
    if generate_html:
        html_file = output_dir / "index.html"
        print(f"\nGenerating HTML: {html_file}")
        from html_generator import generate_align_html
        generate_align_html(config, html_file, authoring=authoring)
        # The standalone align viewer loads Leaflet locally; copy it in.
        copy_leaflet_assets(output_dir)

    print(f"\nOutput generation complete!")
    print(f"  Manifest: {manifest_file}")
    if generate_html:
        print(f"  HTML: {output_dir / 'index.html'}")
    print(f"  Regions: {len(regions_data)}")

    total_track_length_ft = 0.0
    total_track_length_m = 0.0
    for region in regions_data:
        region_length_ft = sum(s.length_ft for s in region.sections)
        region_length_m = sum(s.length_m for s in region.sections)
        total_track_length_ft += region_length_ft
        total_track_length_m += region_length_m
        # Convert to miles for display
        region_length_miles = region_length_ft / 5280
        print(f"    - {region.id}: {len(region.sections)} sections, {len(region.signals)} signals, {region_length_miles:.1f} miles ({region_length_m/1000:.1f} km)")

    # Print grand total
    total_miles = total_track_length_ft / 5280
    total_km = total_track_length_m / 1000
    print(f"\n  Total track length: {total_miles:.1f} miles ({total_km:.1f} km)")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Generate output files from Run8 Track Mapper config'
    )
    parser.add_argument('config', help='Path to configuration INI file')
    parser.add_argument(
        '--tile_report',
        metavar='FILE',
        help='Generate tile list report to FILE (skip normal output generation)'
    )
    parser.add_argument(
        '--align',
        action='store_true',
        dest='align',
        help='Manual-alignment viewer. This is the DEFAULT (and now the only) viewer, '
             'so this flag is a harmless no-op kept for backward compatibility.'
    )
    parser.add_argument(
        '--production',
        action='store_true',
        dest='production',
        help='Same as the default align viewer but WITHOUT the "Add Label" authoring button '
             '(for hosting the map to end users)'
    )
    parser.add_argument(
        '--world',
        metavar='FILE',
        dest='world',
        help='Run8 world save (.xml) to plot trains/rail vehicles from '
             '(overrides [visualization] world_save in the config)'
    )

    args = parser.parse_args()

    config = parse_config(args.config)

    if args.tile_report:
        generate_tile_report(config, args.tile_report)
    elif args.production:
        generate_output(config, authoring=False, world_save=args.world)  # align viewer, no label authoring
    else:
        generate_output(config, world_save=args.world)  # DEFAULT: manual-alignment viewer
