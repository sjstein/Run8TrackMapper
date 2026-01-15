Goals for today in visualize_switch_network.py:

In the handler for plotting the AI spawn locations, there should be a stub function for enumerating the type int to a string. Below is the enumeration values for that function:
*             0: "Spawn Point",
*             1: "Crew Change",
*             2: "Crew Change & Hold",
*             3: "Passenger",
*             4: "Passenger Crew Change",
*             5: "Passenger Crew Change & Hold",
*             6: "Relinquish",
*             7: "Passenger Relinquish"

Using these values, the AI Location popup window should show the enumerated value next to "Type:" with the integer shown after in parenthesis (for example, "Crew Change (1))