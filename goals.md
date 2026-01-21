# Goals and Feedback

## Completed (January 17, 2026)

### Visual Element Changes
- [x] Track color - configurable via INI `[colors]` section
- [x] Track selected color - configurable
- [x] Switch color - configurable, switches now render differently from regular track
- [x] Industry track color - configurable, tracks change color when industry overlay enabled
- [x] Signal absolute color - configurable (orange default)
- [x] Signal intermediate color - configurable (gold default)
- [x] Signal single head border color - configurable (black default)
- [x] Signal multiple head border color - configurable (light blue default)
- [x] Signals show as directional triangles indicating facing direction
- [x] Pop up details restored for all elements (signals, tracks, industries, AI locations)

### UI Improvements
- [x] Background layer options: OpenStreetMap, Satellite, None - added to control panel
- [x] Mouse position display (lat/lon) in lower right corner
- [x] Opacity slider moved up to avoid overlapping scale legend
- [x] Tile overlay no longer blocks tooltips for underlying elements
- [x] Track section hotspot size increased for easier tooltip activation
- [x] Tooltip font size increased

### Tile Boundaries
- [x] Tile boundaries overlay working with rectangles and center dots
- [x] Corrected tiles shown in red, normal tiles in black
- [x] Only center dots show tile info (rectangles are non-interactive)

## Completed (January 19, 2026)

### Bug
- [x] It appears plotting a semi-circular track section sometimes renders as a straight line. 
See file "bakersfield_track_154_issue.md". As a check, make sure any changes don't affect the current
scheme which plots the section shown in "mojave_track_1510.md"

## Improvements
- [x] Allow a region to load which does not have the AISpecialLocations file without halting execution (similar to signals)
- [x] Allow zooming in closer
- [x] Added highlight color when hovering over track section
- [x] Specify terrain tile location for each region 

## Major addition request (January 20, 2026):
Add a command-line option to output_generator.py to generate the plots in a local tile-based coordinate system (abandon 
the conversion to lat/lon). I believe this will generate a "simulator-accurate" representation of the track network 
without trying to overlay on real-world coordinates (which is causing issues). This plotting would involve specifying a 
reference tile to start from, then build out from there using the track sections and nodes just like we did previously. 
The difference is there is no need to convert to lat/lon. 
The plots will be done in the local coordinate system of each tile, and since we know the tile 
orientation, we should be able to draw the same set of data - just independent of geographic coordinates.

Example tile orientation 

            [209,-9]
[208,-10]   [209, -10]  [210, -10]
            [209, -11]

We will need to establish a tile size in meters. That may take some trial and error, but we can start with the following
values:
Maximum X range (842.3m) represents tile width
Maximum Z range (1023.2m) represents tile height

There should be a new configuration section in the configuration file named [tile_based_plot]
with values for home_tile, tile_width, tile_height

The output should be basically the same as the current output_generator.py, but if a tile-based plot is generated, 
there is no need to load any background (openstreet or satellite)

