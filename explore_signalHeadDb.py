#!/usr/bin/env python3

import cmd
import struct
import sys
from typing import List, Optional, Tuple
from collections import Counter


# ===== Helper Functions for Binary Parsing =====

def rotl16(v: int, r: int) -> int:
    """Rotate left 16-bit value by r bits"""
    v &= 0xFFFF  # Ensure 16-bit
    return ((v << r) | (v >> (16 - r))) & 0xFFFF


def read_r8_string(f) -> str:
    """Parse R8String: Int32 byte length + obfuscated UTF-16LE characters

    Strings are obfuscated using a rotation on each 16-bit character.
    """
    byte_length = struct.unpack('<i', f.read(4))[0]
    if byte_length < 0 or byte_length > 10000:
        raise ValueError(f"Invalid string length: {byte_length}")
    if byte_length == 0:
        return ""

    # Read raw bytes
    raw_bytes = f.read(byte_length)

    # Decode as array of ushorts (uint16)
    num_chars = byte_length // 2
    ushorts = struct.unpack(f'<{num_chars}H', raw_bytes)

    # Deobfuscate: rotate each character right by some amount
    # (We rotate left in the function, but to decode we need to reverse the encoding)
    # Try common rotation values - typically 1, 2, 4, 8
    # Let's start with rotation = 1 (most common)
    rotation = 12
    decoded_chars = []
    for char_val in ushorts:
        # To decode, we rotate in the opposite direction
        # If encoded with RotL, we decode with RotR (which is RotL with 16-r)
        decoded = rotl16(char_val, 16 - rotation)
        decoded_chars.append(decoded)

    # Convert to string
    decoded_bytes = struct.pack(f'<{num_chars}H', *decoded_chars)
    return decoded_bytes.decode('utf-16-le', errors='replace')


def read_vector3(f) -> Tuple[float, float, float]:
    """Parse Vector3: three floats (x, y, z)"""
    return struct.unpack('<fff', f.read(12))


def read_tile_index(f) -> Tuple[int, int]:
    """Parse TileIndex: two integers (x, z)"""
    return struct.unpack('<ii', f.read(8))


# ===== Data Classes =====

class SignalSwitchConnector:
    """Represents a signal-switch connection"""

    def __init__(self, f):
        self.reserved = struct.unpack('<i', f.read(4))[0]
        self.switch_index = struct.unpack('<i', f.read(4))[0]
        self.clear_if_thrown_normal = bool(struct.unpack('<B', f.read(1))[0])

    def __str__(self):
        return (f"SignalSwitchConnector(switch={self.switch_index}, "
                f"clear_if_normal={self.clear_if_thrown_normal})")


class Route:
    """Represents a signal route with version-dependent fields"""

    def __init__(self, f):
        # Core fields
        self.version = struct.unpack('<i', f.read(4))[0]
        self.route_name = read_r8_string(f)
        self.route_max_mph = struct.unpack('<i', f.read(4))[0]

        # Block detector indices array
        self.block_detector_count = struct.unpack('<i', f.read(4))[0]
        self.block_detector_indices = []
        if self.block_detector_count > 0:
            self.block_detector_indices = list(struct.unpack(
                f'<{self.block_detector_count}i',
                f.read(4 * self.block_detector_count)
            ))

        # Previous signal indices array
        self.prev_signal_count = struct.unpack('<i', f.read(4))[0]
        self.prev_signal_indices = []
        if self.prev_signal_count > 0:
            self.prev_signal_indices = list(struct.unpack(
                f'<{self.prev_signal_count}i',
                f.read(4 * self.prev_signal_count)
            ))

        # Signal switch connectors array
        self.switch_connector_count = struct.unpack('<i', f.read(4))[0]
        self.switch_connectors = []
        for _ in range(self.switch_connector_count):
            self.switch_connectors.append(SignalSwitchConnector(f))

        # Unknown bools (5 before ReadFromDatabasePrefix) - unkBool1-5
        self.unknown_bools_pre = []
        for _ in range(5):
            self.unknown_bools_pre.append(bool(struct.unpack('<B', f.read(1))[0]))

        # ReadFromDatabasePrefix
        self.read_from_db_prefix = struct.unpack('<i', f.read(4))[0]

        # Conditional unknown bool (only when version is 2) - unkVersion2Only
        if self.version == 2:
            self.unknown_bool_version2 = bool(struct.unpack('<B', f.read(1))[0])
        else:
            self.unknown_bool_version2 = None

        # Unknown bools (12 after, regardless of version) - unkBool6-17
        self.unknown_bools_post = []
        for _ in range(12):
            self.unknown_bools_post.append(bool(struct.unpack('<B', f.read(1))[0]))

        # Is Diverging
        self.is_diverging = bool(struct.unpack('<B', f.read(1))[0])

        # ERouteSpeedClass (Byte/enum)
        self.route_speed_class = struct.unpack('<B', f.read(1))[0]

    def __str__(self):
        return (f"Route(name='{self.route_name}', mph={self.route_max_mph}, "
                f"version={self.version}, diverging={self.is_diverging})")


class SignalHead:
    """Represents a signal head with all its data"""

    def __init__(self, f):
        # Header
        self.reserved = struct.unpack('<i', f.read(4))[0]

        # Signal indices array
        self.signal_index_count = struct.unpack('<i', f.read(4))[0]
        self.signal_indices = []
        if self.signal_index_count > 0:
            self.signal_indices = list(struct.unpack(
                f'<{self.signal_index_count}i',
                f.read(4 * self.signal_index_count)
            ))

        # Routes array
        self.route_count = struct.unpack('<i', f.read(4))[0]
        self.routes = []
        for _ in range(self.route_count):
            self.routes.append(Route(f))

        # Signal properties
        self.signal_index = struct.unpack('<i', f.read(4))[0]
        self.is_absolute = bool(struct.unpack('<B', f.read(1))[0])
        self.model_name = read_r8_string(f)
        self.position = read_vector3(f)
        self.rotation_degrees_y = struct.unpack('<f', f.read(4))[0]
        self.tile_xz = read_tile_index(f)
        self.least_restrictive_state = struct.unpack('<i', f.read(4))[0]

        # Boolean flags
        self.is_advance_diverging = bool(struct.unpack('<B', f.read(1))[0])
        self.unknown_bool_1 = bool(struct.unpack('<B', f.read(1))[0])
        self.is_switch_indicator = bool(struct.unpack('<B', f.read(1))[0])
        self.unknown_bool_2 = bool(struct.unpack('<B', f.read(1))[0])
        self.unknown_bool_3 = bool(struct.unpack('<B', f.read(1))[0])
        self.is_dwarf = bool(struct.unpack('<B', f.read(1))[0])

    def __str__(self):
        return (f"SignalHead(index={self.signal_index}, model='{self.model_name}', "
                f"tile={self.tile_xz}, routes={len(self.routes)})")


class SignalDatabase:
    """Parser for Run8 SignalHeadDatabase.r8 binary format"""

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.signal_heads: List[SignalHead] = []
        self.reserved_header = 0
        self._parse()

    def _parse(self):
        """Parse the binary signal database file"""
        try:
            with open(self.filepath, 'rb') as f:
                # Header
                self.reserved_header = struct.unpack('<i', f.read(4))[0]
                signal_head_count = struct.unpack('<i', f.read(4))[0]

                print(f"Parsing {signal_head_count} signal heads...")

                # Parse each signal head
                for i in range(signal_head_count):
                    try:
                        signal_head = SignalHead(f)
                        self.signal_heads.append(signal_head)

                        # Progress indicator every 10 signals
                        if (i + 1) % 10 == 0:
                            print(f"  Parsed {i + 1}/{signal_head_count} signals...")

                    except Exception as e:
                        print(f"ERROR parsing signal head {i}: {e}")
                        print(f"  File position: {f.tell()}")
                        raise

                # Check if we're at end of file
                remaining_bytes = f.read()
                remaining = len(remaining_bytes)
                if remaining > 0:
                    print(f"WARNING: {remaining} bytes remaining after parsing")
                    print(f"  First 50 bytes: {remaining_bytes[:50].hex()}")
                else:
                    print(f"SUCCESS: Reached end of file (no remaining bytes)")

        except Exception as e:
            print(f"FATAL ERROR during parsing: {e}")
            raise


# ===== Interactive Explorer =====

class SignalDatabaseExplorer(cmd.Cmd):
    """Interactive command-line explorer for SignalHeadDatabase.r8"""

    intro = """
================================================================
         Run8 SignalHeadDatabase Explorer
  Type 'help' or '?' to list commands
================================================================
"""
    prompt = 'signaldb> '

    def __init__(self, db: SignalDatabase):
        super().__init__()
        self.db = db
        self.current_signal = None

    # ===== Basic Info Commands =====

    def do_info(self, arg):
        """Show database summary information"""
        total_signals = len(self.db.signal_heads)
        total_routes = sum(len(s.routes) for s in self.db.signal_heads)

        # Count tiles
        all_tiles = set()
        for signal in self.db.signal_heads:
            all_tiles.add(signal.tile_xz)

        # Count model types
        model_counts = Counter(s.model_name for s in self.db.signal_heads)

        # Count signal types
        absolute_count = sum(1 for s in self.db.signal_heads if s.is_absolute)
        dwarf_count = sum(1 for s in self.db.signal_heads if s.is_dwarf)
        switch_indicator_count = sum(1 for s in self.db.signal_heads if s.is_switch_indicator)

        print(f"\n{'='*60}")
        print(f"SignalDatabase: {self.db.filepath}")
        print(f"{'='*60}")
        print(f"Total Signal Heads:      {total_signals}")
        print(f"Total Routes:            {total_routes}")
        print(f"Unique Tiles:            {len(all_tiles)}")
        print(f"\nSignal Types:")
        print(f"  Absolute signals:      {absolute_count}")
        print(f"  Dwarf signals:         {dwarf_count}")
        print(f"  Switch indicators:     {switch_indicator_count}")
        print(f"\nTop 10 Model Types:")
        for model, count in model_counts.most_common(10):
            print(f"  {model[:40]:<40} {count:4d}")
        print(f"{'='*60}\n")

    def do_stats(self, arg):
        """Show detailed statistics about the database"""
        # Route speed distribution
        route_speeds = []
        for signal in self.db.signal_heads:
            for route in signal.routes:
                route_speeds.append(route.route_max_mph)

        speed_counts = Counter(route_speeds)

        # Tile usage
        tile_usage = Counter()
        for signal in self.db.signal_heads:
            tile_usage[signal.tile_xz] += 1

        print(f"\n{'='*60}")
        print("Route Speed Distribution (MPH):")
        print(f"{'='*60}")
        for speed, count in sorted(speed_counts.items())[:20]:
            print(f"  {speed:3d} MPH: {count:4d} routes")

        print(f"\n{'='*60}")
        print("Top 10 Most Used Tiles:")
        print(f"{'='*60}")
        for tile, count in tile_usage.most_common(10):
            print(f"  Tile ({tile[0]:3d}, {tile[1]:3d}): {count:4d} signals")
        print()

    # ===== Signal Browsing Commands =====

    def do_list(self, arg):
        """List signals with optional filtering
        Usage:
          list [start] [count]          - List signals starting from index
          list tile <x> <z>             - List signals on specific tile
        """
        args = arg.split()

        if not args:
            # Default: show first 20 signals
            self._list_signals(0, 20)
        elif args[0] == 'tile':
            if len(args) != 3:
                print("Usage: list tile <x> <z>")
                return
            try:
                tile_x, tile_z = int(args[1]), int(args[2])
                self._list_signals_by_tile(tile_x, tile_z)
            except ValueError:
                print("Error: Tile coordinates must be integers")
        else:
            # List from start index
            try:
                start = int(args[0])
                count = int(args[1]) if len(args) > 1 else 20
                self._list_signals(start, count)
            except ValueError:
                print("Error: Invalid arguments")
                print(self.do_list.__doc__)

    def _list_signals(self, start: int, count: int):
        """List signals in a range"""
        end = min(start + count, len(self.db.signal_heads))

        if start >= len(self.db.signal_heads):
            print(f"Error: Start index {start} out of range (0-{len(self.db.signal_heads)-1})")
            return

        print(f"\nSignals {start} to {end-1}:")
        print(f"{'Index':<8} {'Model':<35} {'Tile':<15} {'Routes':<8} {'Type'}")
        print("-" * 90)

        for i in range(start, end):
            signal = self.db.signal_heads[i]
            model = signal.model_name[:32]
            tile_str = f"({signal.tile_xz[0]},{signal.tile_xz[1]})"

            signal_type = []
            if signal.is_absolute:
                signal_type.append("ABS")
            if signal.is_dwarf:
                signal_type.append("DWARF")
            if signal.is_switch_indicator:
                signal_type.append("SWITCH")
            type_str = ",".join(signal_type) if signal_type else "-"

            print(f"{i:<8} {model:<35} {tile_str:<15} {len(signal.routes):<8} {type_str}")

        print()

    def _list_signals_by_tile(self, tile_x: int, tile_z: int):
        """List signals on a specific tile"""
        matching = []
        for idx, signal in enumerate(self.db.signal_heads):
            if signal.tile_xz == (tile_x, tile_z):
                matching.append((idx, signal))

        if not matching:
            print(f"No signals found on tile ({tile_x}, {tile_z})")
            return

        print(f"\nSignals on tile ({tile_x}, {tile_z}): {len(matching)} found")
        print(f"{'Index':<8} {'Model':<35} {'Routes':<8} {'Type'}")
        print("-" * 70)

        for idx, signal in matching:
            model = signal.model_name[:32]
            signal_type = []
            if signal.is_absolute:
                signal_type.append("ABS")
            if signal.is_dwarf:
                signal_type.append("DWARF")
            type_str = ",".join(signal_type) if signal_type else "-"

            print(f"{idx:<8} {model:<35} {len(signal.routes):<8} {type_str}")
        print()

    def do_show(self, arg):
        """Show detailed information about a specific signal
        Usage: show <index>
        Example: show 0
        """
        if not arg:
            print("Usage: show <index>")
            return

        try:
            idx = int(arg)
            if idx < 0 or idx >= len(self.db.signal_heads):
                print(f"Error: Index {idx} out of range (0-{len(self.db.signal_heads)-1})")
                return

            signal = self.db.signal_heads[idx]
            self.current_signal = idx
            self._show_signal(idx, signal)
        except ValueError:
            print("Error: Index must be an integer")

    def _show_signal(self, idx: int, signal: SignalHead):
        """Display detailed signal information"""
        print(f"\n{'='*70}")
        print(f"SignalHead[{idx}] (signal_index={signal.signal_index})")
        print(f"{'='*70}")
        print(f"Model name:              {signal.model_name}")
        print(f"Position:                ({signal.position[0]:.2f}, {signal.position[1]:.2f}, {signal.position[2]:.2f})")
        print(f"Rotation (Y degrees):    {signal.rotation_degrees_y:.2f}")
        print(f"Tile:                    ({signal.tile_xz[0]}, {signal.tile_xz[1]})")
        print(f"Is absolute:             {signal.is_absolute}")
        print(f"Is dwarf:                {signal.is_dwarf}")
        print(f"Is switch indicator:     {signal.is_switch_indicator}")
        print(f"Is advance diverging:    {signal.is_advance_diverging}")
        print(f"Least restrictive state: {signal.least_restrictive_state}")
        print(f"Number of routes:        {len(signal.routes)}")

        if signal.signal_indices:
            print(f"Signal indices:          {', '.join(map(str, signal.signal_indices))}")

        if signal.routes:
            print(f"\n{'Routes:':<70}")
            print(f"{'-'*70}")
            for i, route in enumerate(signal.routes):
                print(f"\nRoute {i}: {route.route_name}")
                print(f"  Max speed:           {route.route_max_mph} MPH")
                print(f"  Version:             {route.version}")
                print(f"  Is diverging:        {route.is_diverging}")
                print(f"  Speed class:         {route.route_speed_class}")

                if route.block_detector_indices:
                    print(f"  Block detectors:     {', '.join(map(str, route.block_detector_indices))}")
                if route.prev_signal_indices:
                    print(f"  Previous signals:    {', '.join(map(str, route.prev_signal_indices))}")
                if route.switch_connectors:
                    print(f"  Switch connectors:   {len(route.switch_connectors)}")
                    for sc in route.switch_connectors:
                        print(f"    - Switch {sc.switch_index} (clear_if_normal={sc.clear_if_thrown_normal})")

        print(f"\n{'='*70}\n")

    def do_search(self, arg):
        """Search for signals by various criteria
        Usage:
          search index <signal_index>  - Find signal by its index field
          search tile <x> <z>          - Find signals on specific tile
          search model <substring>     - Find signals by model name
        """
        args = arg.split()

        if len(args) < 2:
            print("Usage: search <criterion> <value>")
            print(self.do_search.__doc__)
            return

        criterion = args[0].lower()

        if criterion == 'index':
            try:
                signal_index = int(args[1])
                self._search_by_index(signal_index)
            except ValueError:
                print("Error: Signal index must be an integer")

        elif criterion == 'tile':
            if len(args) < 3:
                print("Usage: search tile <x> <z>")
                return
            try:
                tile_x, tile_z = int(args[1]), int(args[2])
                self._list_signals_by_tile(tile_x, tile_z)
            except ValueError:
                print("Error: Tile coordinates must be integers")

        elif criterion == 'model':
            model_substring = ' '.join(args[1:])
            self._search_by_model(model_substring)

        else:
            print(f"Unknown search criterion: {criterion}")
            print(self.do_search.__doc__)

    def _search_by_index(self, signal_index: int):
        """Search for signal by its index field"""
        for idx, signal in enumerate(self.db.signal_heads):
            if signal.signal_index == signal_index:
                print(f"\nFound: SignalHead[{idx}] has signal_index={signal_index}")
                self._show_signal(idx, signal)
                return

        print(f"No signal found with signal_index={signal_index}")

    def _search_by_model(self, substring: str):
        """Search for signals by model name"""
        matching = []
        for idx, signal in enumerate(self.db.signal_heads):
            if substring.lower() in signal.model_name.lower():
                matching.append((idx, signal))

        if not matching:
            print(f"No signals found with model name containing '{substring}'")
            return

        print(f"\nSignals with model name containing '{substring}': {len(matching)} found")
        print(f"{'Index':<8} {'Model':<50}")
        print("-" * 70)

        for idx, signal in matching[:50]:
            print(f"{idx:<8} {signal.model_name}")

        if len(matching) > 50:
            print(f"\n(Showing first 50 of {len(matching)} signals)")
        print()

    # ===== Exit Commands =====

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
        print("Usage: explore_signalHeadDb.py <SignalHeadDatabase.r8>")
        sys.exit(1)

    db_file = sys.argv[1]

    print(f"Loading {db_file}...")
    try:
        db = SignalDatabase(db_file)
        print(f"Loaded {len(db.signal_heads)} signal heads successfully.\n")
    except Exception as e:
        print(f"Error loading database: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    explorer = SignalDatabaseExplorer(db)
    explorer.cmdloop()


if __name__ == '__main__':
    main()
