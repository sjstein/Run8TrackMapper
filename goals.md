# Goals and Feedback

# I've noticed a bug in some of the track section renderings coming from the penn region
(C:\Run8Studios\Run8 Train Simulator V3\Content\V3Routes\NS_South_Fork_Secondary\TrackDatabase.r8)

For example, track section 1022 is rendering as a 1022.9m section, but the arc_len value shows 14m. 
Furthermore, the tangent value shows Node 0 heading Eastwards, yet the rendering has the section going almost North South.

The coordinates for Node0 are odd in that they are not directly reciprocal of each other. 

======================================================================
TrackSection[956] (index=1022)
======================================================================
Number of nodes:         2
Section type:            Regular section
Spans multiple tiles:    No
Track type:              25
Switch position:         0
Retarder speed (mph):    -1.0
Is occupied:             False
Is CTC switch:           False
Next sections:           1021, 1023

Nodes:
----------------------------------------------------------------------

Node 0:
  Belongs to track:      1022
  Tile index:            (5, 2)
  Num Segments:          1
  Is Selected:           False
  Position:              (623.24, 647.07, -4.03)
  End position:          (636.85, 646.87, -1026.86)
  Tangent (deg):         (-0.82, 103.43, 0.00)
  Is switch node:        False
  Is reverse path:       False
  Curve deg:             0.00
  Curve sign:            1
  Radius (m):            0.00
  Arc length (m):        14.00

Node 1:
  Belongs to track:      1022
  Tile index:            (5, 2)
  Num Segments:          0
  Is Selected:           False
  Position:              (636.85, 646.87, -0.78)
  End position:          (623.24, 647.07, -4.03)
  Tangent (deg):         (0.82, 283.43, 0.00)
  Is switch node:        False
  Is reverse path:       False
  Curve deg:             0.00
  Curve sign:            0
  Radius (m):            0.00
  Arc length (m):        14.00


