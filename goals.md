Goals for today in visualize_switch_network.py:

Background: For track sections that are not switches, we should be generating the plotted segments based on the node which is non-zero.  
1. First, determine how we are currently using the node records within the track database in terms of plotting. How do we decide which node to use to plot the extents? 
2. For sections with 2 nodes, if we are *not* utilizing the number Num Segments attribute, we should adapt its use as follows:
* If both node.num_segments = 0, flag as an error
* If both node.num_segments > 0, flag as an error
* For the case where one node is non-zero, and the other is zero, use the node.position and node.end_position values to determine the end-points of the line