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
from typing import List, Optional


# Standard filenames within each region directory
TRACK_DB_FILENAME = "TrackDatabase.r8"
SIGNAL_DB_FILENAME = "SignalHeadDatabase.r8"
AI_LOCATIONS_FILENAME = "AiSpecialLocations.r8"


@dataclass
class ColorConfig:
    """Color configuration for visualization elements"""
    track: str = "#0066cc"              # Regular track sections
    track_selected: str = "#ff0000"     # Selected track sections
    switch: str = "#800080"             # Switch/turnout sections (purple)
    industry_track: str = "#00aa00"     # Industry track sections (green)
    signal_absolute: str = "#FF6B35"    # Absolute signals (orange)
    signal_intermediate: str = "#FFD700"  # Intermediate signals (gold)
    signal_border_single: str = "#000000"  # Single head signal border (black)
    signal_border_stacked: str = "#87CEEB"  # Multiple head signal border (light blue)


@dataclass
class RegionConfig:
    """Configuration for a single region"""
    id: str  # e.g., "mojave" (from section name [region.mojave])
    display_name: str
    route_prefix: int
    directory: Path
    enabled_by_default: bool = False

    @property
    def track_database(self) -> Path:
        return self.directory / TRACK_DB_FILENAME

    @property
    def signal_database(self) -> Path:
        return self.directory / SIGNAL_DB_FILENAME

    @property
    def ai_locations_database(self) -> Path:
        return self.directory / AI_LOCATIONS_FILENAME


@dataclass
class VisualizationConfig:
    """Complete configuration for a multi-region visualization"""
    name: str
    tile_corrections: Path
    industry_db: Path
    output_dir: Path
    regions: List[RegionConfig] = field(default_factory=list)
    colors: ColorConfig = field(default_factory=ColorConfig)


class ConfigError(Exception):
    """Raised when configuration is invalid"""
    pass


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

    # Parse [visualization] section
    if 'visualization' not in parser:
        errors.append("[visualization] section is required")
    else:
        viz = parser['visualization']
        name = viz.get('name', '').strip()
        if not name:
            errors.append("[visualization] name is required")

        tile_corrections_str = viz.get('tile_corrections', '').strip()
        if not tile_corrections_str:
            errors.append("[visualization] tile_corrections is required")

        industry_db_str = viz.get('industry_db', '').strip()
        if not industry_db_str:
            errors.append("[visualization] industry_db is required")

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

        if display_name and directory_str:
            regions.append(RegionConfig(
                id=region_id,
                display_name=display_name,
                route_prefix=route_prefix,
                directory=Path(directory_str),
                enabled_by_default=enabled_by_default
            ))

    # If we have basic parsing errors, raise now
    if errors:
        raise ConfigError("Configuration errors:\n  - " + "\n  - ".join(errors))

    # Parse [colors] section (optional - use defaults if not present)
    colors = ColorConfig()
    if 'colors' in parser:
        color_section = parser['colors']
        colors = ColorConfig(
            track=color_section.get('track', colors.track).strip(),
            track_selected=color_section.get('track_selected', colors.track_selected).strip(),
            switch=color_section.get('switch', colors.switch).strip(),
            industry_track=color_section.get('industry_track', colors.industry_track).strip(),
            signal_absolute=color_section.get('signal_absolute', colors.signal_absolute).strip(),
            signal_intermediate=color_section.get('signal_intermediate', colors.signal_intermediate).strip(),
            signal_border_single=color_section.get('signal_border_single', colors.signal_border_single).strip(),
            signal_border_stacked=color_section.get('signal_border_stacked', colors.signal_border_stacked).strip(),
        )

    # Build the config object
    config = VisualizationConfig(
        name=name,
        tile_corrections=Path(tile_corrections_str),
        industry_db=Path(industry_db_str),
        output_dir=Path(output_dir_str),
        regions=regions,
        colors=colors
    )

    # Validate that all files exist
    file_errors = []

    if not config.tile_corrections.exists():
        file_errors.append(f"[visualization] tile_corrections file not found: {config.tile_corrections}")

    if not config.industry_db.exists():
        file_errors.append(f"[visualization] industry_db file not found: {config.industry_db}")

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
                file_errors.append(f"[region.{region.id}] AI locations database not found: {region.ai_locations_database}")

    if file_errors:
        raise ConfigError("Missing files:\n  - " + "\n  - ".join(file_errors))

    return config


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

# Path to industry database (global, filtered by route_prefix per region)
# Always named Config.ind, located in the Regions directory
industry_db = C:\\Run8Studios\\Run8 Train Simulator V3\\Content\\V3Routes\\Regions\\SouthernCA\\Config.ind

[region.mojave]
# Human-readable name for this region
display_name = Mojave Subdivision

# Route prefix integer for filtering industries
route_prefix = 1

# Directory containing TrackDatabase.r8, SignalHeadDatabase.r8, AiSpecialLocations.r8
directory = C:\\Run8Studios\\Run8 Train Simulator V3\\Content\\V3Routes\\BNSF_MojaveSub

# Whether this region is enabled by default when the map loads
enabled_by_default = true

[region.barstow]
display_name = Barstow Subdivision
route_prefix = 2
directory = C:\\Run8Studios\\Run8 Train Simulator V3\\Content\\V3Routes\\BNSF_BarstowSub
enabled_by_default = false

# Add more [region.*] sections as needed...

[colors]
# All colors are optional - defaults are shown below
# Colors can be hex (#RRGGBB) or named colors

# Track colors
track = #0066cc
track_selected = #ff0000
switch = #800080
industry_track = #00aa00

# Signal colors
signal_absolute = #FF6B35
signal_intermediate = #FFD700
signal_border_single = #000000
signal_border_stacked = #87CEEB

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
            print(f"  Industry DB: {config.industry_db}")
            print(f"  Output directory: {config.output_dir}")
            print(f"  Regions: {len(config.regions)}")
            for region in config.regions:
                print(f"    - {region.display_name} (prefix={region.route_prefix}, enabled={region.enabled_by_default})")
                print(f"      Directory: {region.directory}")
        except ConfigError as e:
            print(f"ERROR: {e}")
            sys.exit(1)
