# Current goal : Fix search function for tile-based renderings
With respect to the tool which starts at output_generator.py

### The current search -> Industry functionality appears to be broken in a few ways:
- When user types in search field, the suggestions which begin popping up all have the text "(undefined)" at the end of
the industry name (for exampled "DDD - Desert Diamond Facility (undefined)"). Ideally the Region the industry is in
should show within the parenthesis - if that isn't possible, then that message should eliminate the undefined part.
- When an industry is selected by clicking on it in the search list, the view moves but does not center on the industry
(which is the desired action)

## Secondary Goal: Fix industry labels for tracks which cross tile boundaries 

### For industry tracks which cross a tile boundary, the industry tag which is display appears to be placed incorrectly
- Examine the code which locates that text and determine if it is taking into account a track segment which may cross
a tile boundary.

