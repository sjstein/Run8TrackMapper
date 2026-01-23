# Goals and Feedback

# Changes to config_parser.py

- We already define these constants:
TRACK_DB_FILENAME = "TrackDatabase.r8"
SIGNAL_DB_FILENAME = "SignalHeadDatabase.r8"
AI_LOCATIONS_FILENAME = "AiSpecialLocations.r8"

New ones should be added:
INDUSTRY_DB_FILENAME = "Config.ind"
TERRAIN_DIR_NAME = "TerrainTiles"

A new entry should be added to the config.ini structure under [visualization] as follows:
region_dir

As an example, for SouthernCA region the entry would be:
region_dir = C:\Run8Studios\Run8 Train Simulator V3\Content\V3Routes\Regions\SouthernCA

We can then derive the location of the TerrainTiles directory, and the industry database.
So the industry_db entry in the config.ini can be removed, and anywhere the industry_db is referred to is replaced with
[region_dir]\INDUSTRY_DB_FILENAME

Similarly, instead of defining a constant in region_extrator.py as follows:
TILE_DIR = r'C:\Run8Studios\Run8 Train Simulator V3\Content\V3Routes\Regions\SouthernCA\TerrainTiles'

The location of TerrainTiles will be [region_dir]\TERRAIN_DIR_NAME

These changes will need to be incorporated into region_extractor.py and possibly others which are part of the
output_generator.py toolchain

