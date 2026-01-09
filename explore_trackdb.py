#!/usr/bin/env python3

import cmd
import struct
import sys
from typing import List, Optional, Tuple
from collections import Counter


class TrackNode:
    """Represents a single track node in a section"""
    def __init__(self, data: bytes, offset: int = 0):
        fmt = '<i ii fff fff fff i BB f i f f i i B'
        size = struct.calcsize(fmt)
        unpacked = struct.unpack(fmt, data[offset:offset+size])

        self.node_unk = unpacked[0]
        self.tile_index = (unpacked[1], unpacked[2])
        self.position = unpacked[3:6]
        self.tan_deg = unpacked[6:9]
        self.end_position = unpacked[9:12]
        self.index = unpacked[12]
        self.is_switch_node = bool(unpacked[13])
        self.is_reverse_path = bool(unpacked[14])
        self.curve_deg = unpacked[15]
        self.curve_sign = unpacked[16]
        self.radius_meters = unpacked[17]
        self.arcLen_meters = unpacked[18]
        self.num_segments = unpacked[19]
        self.belongs_to_track_index = unpacked[20]
        self.is_selected = bool(unpacked[21])

    @staticmethod
    def size():
        return 79

    def __str__(self):
        return (f"TrackNode(tile={self.tile_index}, pos={self.position}, "
                f"switch={self.is_switch_node}, reverse={self.is_reverse_path})")


class TrackSection:
    """Represents a track section with nodes and metadata"""
    def __init__(self):
        self.section_unk = 0
        self.nodes: List[TrackNode] = []
        self.index = 0
        self.switch_pos = 0
        self.next_section: List[int] = []
        self.track_type = 0
        self.retarder_mph = 0.0
        self.is_occupied = False
        self.switch_stand_left_side = False
        self.switch_stand_type = 0
        self.is_ctc_switch = False

    def spans_multiple_tiles(self) -> bool:
        """Check if section has nodes on different tiles"""
        tiles = set(node.tile_index for node in self.nodes)
        return len(tiles) > 1

    def get_tiles(self) -> set:
        """Get all unique tiles this section touches"""
        return set(node.tile_index for node in self.nodes)


class TrackDatabase:
    """Parser for Run8 TrackDatabase.r8 binary format"""

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.sections: List[TrackSection] = []
        self.unknown_hdr = 0
        self._parse()

    def _parse(self):
        """Parse the binary track database file"""
        with open(self.filepath, 'rb') as f:
            self.unknown_hdr = struct.unpack('<i', f.read(4))[0]
            num_sections = struct.unpack('<i', f.read(4))[0]

            for _ in range(num_sections):
                section = TrackSection()
                section.section_unk = struct.unpack('<i', f.read(4))[0]
                number_of_nodes = struct.unpack('<i', f.read(4))[0]

                node_size = TrackNode.size()
                for _ in range(number_of_nodes):
                    node_data = f.read(node_size)
                    section.nodes.append(TrackNode(node_data))

                section.index = struct.unpack('<i', f.read(4))[0]
                section.switch_pos = struct.unpack('<B', f.read(1))[0]
                num_section_indices = struct.unpack('<i', f.read(4))[0]
                section.next_section = list(struct.unpack(f'<{num_section_indices}i',
                                                         f.read(4 * num_section_indices)))
                section.track_type = struct.unpack('<B', f.read(1))[0]
                section.retarder_mph = struct.unpack('<d', f.read(8))[0]
                section.is_occupied = bool(struct.unpack('<B', f.read(1))[0])
                section.switch_stand_left_side = bool(struct.unpack('<B', f.read(1))[0])
                section.switch_stand_type = struct.unpack('<i', f.read(4))[0]
                section.is_ctc_switch = bool(struct.unpack('<B', f.read(1))[0])

                self.sections.append(section)


class TrackDatabaseExplorer(cmd.Cmd):
    """Interactive command-line explorer for TrackDatabase.r8"""

    intro = """
================================================================
         Run8 TrackDatabase Explorer
  Type 'help' or '?' to list commands
================================================================
"""
    prompt = 'trackdb> '

    def __init__(self, db: TrackDatabase):
        super().__init__()
        self.db = db
        self.current_section = None

    # ===== Basic Info Commands =====

    def do_info(self, arg):
        """Show database summary information"""
        total_sections = len(self.db.sections)
        total_nodes = sum(len(s.nodes) for s in self.db.sections)

        # Count by node type
        node_counts = Counter(len(s.nodes) for s in self.db.sections)
        cross_tile_sections = sum(1 for s in self.db.sections if s.spans_multiple_tiles())

        # Count tiles
        all_tiles = set()
        for section in self.db.sections:
            all_tiles.update(section.get_tiles())

        print(f"\n{'='*60}")
        print(f"TrackDatabase: {self.db.filepath}")
        print(f"{'='*60}")
        print(f"Total Sections:          {total_sections}")
        print(f"Total Nodes:             {total_nodes}")
        print(f"Unique Tiles:            {len(all_tiles)}")
        print(f"Cross-tile Sections:     {cross_tile_sections} ({100*cross_tile_sections/total_sections:.1f}%)")
        print(f"\nSection Types:")
        for node_count, count in sorted(node_counts.items()):
            section_type = "turnout" if node_count == 4 else "section"
            print(f"  {node_count}-node {section_type}: {count}")
        print(f"{'='*60}\n")

    def do_stats(self, arg):
        """Show detailed statistics about the database"""
        # Track types
        track_types = Counter(s.track_type for s in self.db.sections)

        # Tiles
        tile_usage = Counter()
        for section in self.db.sections:
            for node in section.nodes:
                tile_usage[node.tile_index] += 1

        print(f"\n{'='*60}")
        print("Track Type Distribution:")
        print(f"{'='*60}")
        for track_type, count in sorted(track_types.items(), key=lambda x: -x[1])[:10]:
            print(f"  Type {track_type:3d}: {count:4d} sections")

        print(f"\n{'='*60}")
        print("Top 10 Most Used Tiles:")
        print(f"{'='*60}")
        for tile, count in tile_usage.most_common(10):
            print(f"  Tile ({tile[0]:3d}, {tile[1]:3d}): {count:4d} nodes")
        print()

    # ===== Section Browsing Commands =====

    def do_list(self, arg):
        """List sections with optional filtering
        Usage:
          list [start] [count]          - List sections starting from index
          list tile <x> <z>             - List sections on specific tile
          list turnouts                 - List only turnout sections (4 nodes)
          list cross-tile               - List sections spanning multiple tiles
        """
        args = arg.split()

        if not args:
            # Default: show first 20 sections
            self._list_sections(0, 20)
        elif args[0] == 'tile':
            if len(args) != 3:
                print("Usage: list tile <x> <z>")
                return
            try:
                tile_x, tile_z = int(args[1]), int(args[2])
                self._list_sections_by_tile(tile_x, tile_z)
            except ValueError:
                print("Error: Tile coordinates must be integers")
        elif args[0] == 'turnouts':
            self._list_turnouts()
        elif args[0] == 'cross-tile':
            self._list_cross_tile_sections()
        else:
            # List from start index
            try:
                start = int(args[0])
                count = int(args[1]) if len(args) > 1 else 20
                self._list_sections(start, count)
            except ValueError:
                print("Error: Invalid arguments")
                print(self.do_list.__doc__)

    def _list_sections(self, start: int, count: int):
        """List sections in a range"""
        end = min(start + count, len(self.db.sections))

        if start >= len(self.db.sections):
            print(f"Error: Start index {start} out of range (0-{len(self.db.sections)-1})")
            return

        print(f"\nSections {start} to {end-1}:")
        print(f"{'Array Idx':<10} {'Index':<8} {'Nodes':<7} {'Type':<10} {'Tiles':<20} {'Next Sections'}")
        print("-" * 90)

        for i in range(start, end):
            section = self.db.sections[i]
            node_count = len(section.nodes)
            section_type = "turnout" if node_count == 4 else "section"
            tiles = section.get_tiles()
            tiles_str = ", ".join(f"({t[0]},{t[1]})" for t in sorted(tiles))
            if len(tiles_str) > 18:
                tiles_str = tiles_str[:15] + "..."
            next_str = ", ".join(str(n) for n in section.next_section[:3])
            if len(section.next_section) > 3:
                next_str += "..."

            marker = "*" if section.spans_multiple_tiles() else " "
            print(f"{marker}[{i:<7}] {section.index:<8} {node_count:<7} {section_type:<10} {tiles_str:<20} {next_str}")

        print(f"\n(* = spans multiple tiles)\n")

    def _list_sections_by_tile(self, tile_x: int, tile_z: int):
        """List sections on a specific tile"""
        matching = []
        for idx, section in enumerate(self.db.sections):
            for node in section.nodes:
                if node.tile_index == (tile_x, tile_z):
                    matching.append((idx, section))
                    break

        if not matching:
            print(f"No sections found on tile ({tile_x}, {tile_z})")
            return

        print(f"\nSections on tile ({tile_x}, {tile_z}): {len(matching)} found")
        print(f"{'Array Idx':<10} {'Index':<8} {'Nodes':<7} {'Type':<10} {'Spans Tiles'}")
        print("-" * 60)

        for idx, section in matching:
            node_count = len(section.nodes)
            section_type = "turnout" if node_count == 4 else "section"
            spans = "Yes" if section.spans_multiple_tiles() else "No"
            print(f"[{idx:<7}] {section.index:<8} {node_count:<7} {section_type:<10} {spans}")
        print()

    def _list_turnouts(self):
        """List all turnout sections"""
        turnouts = [(idx, s) for idx, s in enumerate(self.db.sections) if len(s.nodes) == 4]

        print(f"\nTurnout sections (4 nodes): {len(turnouts)} found")
        print(f"{'Array Idx':<10} {'Index':<8} {'Tiles':<30} {'CTC'}")
        print("-" * 70)

        for idx, section in turnouts[:50]:  # Limit to 50
            tiles = section.get_tiles()
            tiles_str = ", ".join(f"({t[0]},{t[1]})" for t in sorted(tiles))
            if len(tiles_str) > 28:
                tiles_str = tiles_str[:25] + "..."
            ctc = "Yes" if section.is_ctc_switch else "No"
            print(f"[{idx:<7}] {section.index:<8} {tiles_str:<30} {ctc}")

        if len(turnouts) > 50:
            print(f"\n(Showing first 50 of {len(turnouts)} turnouts)")
        print()

    def _list_cross_tile_sections(self):
        """List sections that span multiple tiles"""
        cross_tile = [(idx, s) for idx, s in enumerate(self.db.sections) if s.spans_multiple_tiles()]

        print(f"\nCross-tile sections: {len(cross_tile)} found")
        print(f"{'Array Idx':<10} {'Index':<8} {'Nodes':<7} {'Tiles'}")
        print("-" * 60)

        for idx, section in cross_tile[:50]:  # Limit to 50
            tiles = sorted(section.get_tiles())
            tiles_str = ", ".join(f"({t[0]},{t[1]})" for t in tiles)
            print(f"[{idx:<7}] {section.index:<8} {len(section.nodes):<7} {tiles_str}")

        if len(cross_tile) > 50:
            print(f"\n(Showing first 50 of {len(cross_tile)} cross-tile sections)")
        print()

    def do_show(self, arg):
        """Show detailed information about a specific section
        Usage: show <array_index>
        Example: show 1603
        """
        if not arg:
            print("Usage: show <array_index>")
            return

        try:
            idx = int(arg)
            if idx < 0 or idx >= len(self.db.sections):
                print(f"Error: Index {idx} out of range (0-{len(self.db.sections)-1})")
                return

            section = self.db.sections[idx]
            self.current_section = idx
            self._show_section(idx, section)
        except ValueError:
            print("Error: Index must be an integer")

    def _show_section(self, array_idx: int, section: TrackSection):
        """Display detailed section information"""
        print(f"\n{'='*70}")
        print(f"TrackSection[{array_idx}] (index={section.index})")
        print(f"{'='*70}")
        print(f"Number of nodes:         {len(section.nodes)}")
        print(f"Section type:            {'Turnout' if len(section.nodes) == 4 else 'Regular section'}")
        print(f"Spans multiple tiles:    {'Yes' if section.spans_multiple_tiles() else 'No'}")
        print(f"Track type:              {section.track_type}")
        print(f"Switch position:         {section.switch_pos}")
        print(f"Retarder speed (mph):    {section.retarder_mph}")
        print(f"Is occupied:             {section.is_occupied}")
        print(f"Is CTC switch:           {section.is_ctc_switch}")
        print(f"Next sections:           {', '.join(map(str, section.next_section))}")

        print(f"\n{'Nodes:':<70}")
        print(f"{'-'*70}")
        for i, node in enumerate(section.nodes):
            print(f"\nNode {i}:")
            print(f"  Belongs to track:      {node.belongs_to_track_index}")
            print(f"  Tile index:            ({node.tile_index[0]}, {node.tile_index[1]})")
            print(f"  Num Segments:          {node.num_segments}")
            print(f"  Is Selected:           {node.is_selected}")
            print(f"  Position:              ({node.position[0]:.2f}, {node.position[1]:.2f}, {node.position[2]:.2f})")
            print(f"  End position:          ({node.end_position[0]:.2f}, {node.end_position[1]:.2f}, {node.end_position[2]:.2f})")
            print(f"  Tangent (deg):         ({node.tan_deg[0]:.2f}, {node.tan_deg[1]:.2f}, {node.tan_deg[2]:.2f})")
            print(f"  Is switch node:        {node.is_switch_node}")
            print(f"  Is reverse path:       {node.is_reverse_path}")
            print(f"  Curve deg:             {node.curve_deg:.2f}")
            print(f"  Curve sign:            {node.curve_sign}")
            print(f"  Radius (m):            {node.radius_meters:.2f}")
            print(f"  Arc length (m):        {node.arcLen_meters:.2f}")

        print(f"\n{'='*70}\n")

    def do_search(self, arg):
        """Search for sections by various criteria
        Usage:
          search index <section_index>  - Find section by its index field
          search tile <x> <z>            - Find sections on specific tile
          search type <track_type>       - Find sections by track type
        """
        args = arg.split()

        if len(args) < 2:
            print("Usage: search <criterion> <value>")
            print(self.do_search.__doc__)
            return

        criterion = args[0].lower()

        if criterion == 'index':
            try:
                section_index = int(args[1])
                self._search_by_index(section_index)
            except ValueError:
                print("Error: Section index must be an integer")

        elif criterion == 'tile':
            if len(args) < 3:
                print("Usage: search tile <x> <z>")
                return
            try:
                tile_x, tile_z = int(args[1]), int(args[2])
                self._list_sections_by_tile(tile_x, tile_z)
            except ValueError:
                print("Error: Tile coordinates must be integers")

        elif criterion == 'type':
            try:
                track_type = int(args[1])
                self._search_by_track_type(track_type)
            except ValueError:
                print("Error: Track type must be an integer")

        else:
            print(f"Unknown search criterion: {criterion}")
            print(self.do_search.__doc__)

    def _search_by_index(self, section_index: int):
        """Search for section by its index field"""
        for idx, section in enumerate(self.db.sections):
            if section.index == section_index:
                print(f"\nFound: TrackSection[{idx}] has index={section_index}")
                self._show_section(idx, section)
                return

        print(f"No section found with index={section_index}")

    def _search_by_track_type(self, track_type: int):
        """Search for sections by track type"""
        matching = [(idx, s) for idx, s in enumerate(self.db.sections) if s.track_type == track_type]

        if not matching:
            print(f"No sections found with track_type={track_type}")
            return

        print(f"\nSections with track_type={track_type}: {len(matching)} found")
        print(f"{'Array Idx':<10} {'Index':<8} {'Nodes':<7} {'Tiles'}")
        print("-" * 60)

        for idx, section in matching[:50]:
            tiles = section.get_tiles()
            tiles_str = ", ".join(f"({t[0]},{t[1]})" for t in sorted(tiles))
            if len(tiles_str) > 28:
                tiles_str = tiles_str[:25] + "..."
            print(f"[{idx:<7}] {section.index:<8} {len(section.nodes):<7} {tiles_str}")

        if len(matching) > 50:
            print(f"\n(Showing first 50 of {len(matching)} sections)")
        print()

    # ===== Navigation Commands =====

    def do_next(self, arg):
        """Show the next connected sections from the current section"""
        if self.current_section is None:
            print("No current section selected. Use 'show <index>' first.")
            return

        section = self.db.sections[self.current_section]

        if not section.next_section:
            print("Current section has no next sections.")
            return

        print(f"\nNext sections from TrackSection[{self.current_section}]:")
        print(f"{'Array Idx':<10} {'Index':<8} {'Nodes':<7} {'Tiles'}")
        print("-" * 60)

        for next_idx in section.next_section:
            # Find the array index for this section index
            for idx, s in enumerate(self.db.sections):
                if s.index == next_idx:
                    tiles = s.get_tiles()
                    tiles_str = ", ".join(f"({t[0]},{t[1]})" for t in sorted(tiles))
                    print(f"[{idx:<7}] {s.index:<8} {len(s.nodes):<7} {tiles_str}")
                    break
        print()

    # ===== Exit Command =====

    def do_exit(self, arg):
        """Exit the explorer"""
        print("\nGoodbye!")
        return True

    def do_quit(self, arg):
        """Exit the explorer"""
        return self.do_exit(arg)

    def do_EOF(self, arg):
        """Exit on Ctrl+D"""
        print()
        return self.do_exit(arg)


def main():
    if len(sys.argv) != 2:
        print("Usage: explore_trackdb.py <TrackDatabase.r8>")
        sys.exit(1)

    db_file = sys.argv[1]

    print(f"Loading {db_file}...")
    try:
        db = TrackDatabase(db_file)
        print(f"Loaded {len(db.sections)} sections successfully.\n")
    except Exception as e:
        print(f"Error loading database: {e}")
        sys.exit(1)

    explorer = TrackDatabaseExplorer(db)
    explorer.cmdloop()


if __name__ == '__main__':
    main()
