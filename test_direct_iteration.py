#!/usr/bin/env python3
"""
Phase 1.1 Validation: Test direct iteration vs graph traversal

This script compares two approaches:
1. Direct iteration: All sections in the track database
2. Graph traversal: Starting from a section and following connections

Goal: Determine if we can safely replace traversal with direct iteration.
"""

import sys
from pathlib import Path

# Import from existing modules
from explore_trackdb import TrackDatabase


def get_all_sections_direct(db: TrackDatabase) -> set:
    """Get all section indices by direct iteration"""
    return {section.index for section in db.sections}


def get_all_sections_traversal(db: TrackDatabase, start_idx: int, max_depth: int = 1000) -> set:
    """
    Get all reachable sections via graph traversal (BFS).
    Uses a very high max_depth to effectively traverse entire graph.
    """
    section_map = {sec.index: sec for sec in db.sections}

    if start_idx not in section_map:
        print(f"Error: Start section {start_idx} not found in database")
        return set()

    visited = set()
    queue = [start_idx]

    while queue:
        current_idx = queue.pop(0)
        if current_idx in visited:
            continue
        visited.add(current_idx)

        if current_idx not in section_map:
            continue

        section = section_map[current_idx]

        # Add all connected sections to queue
        for next_idx in section.next_section:
            if next_idx not in visited:
                queue.append(next_idx)

    return visited


def main():
    if len(sys.argv) < 2:
        print("Usage: test_direct_iteration.py <TrackDatabase.r8> [start_section]")
        print("")
        print("If start_section is omitted, will try to find a good starting point")
        sys.exit(1)

    db_path = sys.argv[1]

    print(f"Loading track database: {db_path}")
    db = TrackDatabase(db_path)
    print(f"Loaded {len(db.sections)} sections")
    print()

    # Get all sections via direct iteration
    direct_sections = get_all_sections_direct(db)
    print(f"Direct iteration: {len(direct_sections)} unique section indices")

    # Find a starting section for traversal
    if len(sys.argv) >= 3:
        start_idx = int(sys.argv[2])
    else:
        # Pick the first section with connections
        start_idx = None
        for section in db.sections:
            if section.next_section:
                start_idx = section.index
                break
        if start_idx is None:
            start_idx = db.sections[0].index if db.sections else 0

    print(f"Starting traversal from section: {start_idx}")

    # Get all reachable sections via traversal
    traversal_sections = get_all_sections_traversal(db, start_idx)
    print(f"Graph traversal: {len(traversal_sections)} reachable sections")
    print()

    # Compare
    only_in_direct = direct_sections - traversal_sections
    only_in_traversal = traversal_sections - direct_sections

    print("=" * 60)
    print("COMPARISON RESULTS")
    print("=" * 60)
    print(f"Sections in both:          {len(direct_sections & traversal_sections)}")
    print(f"Only in direct iteration:  {len(only_in_direct)}")
    print(f"Only in traversal:         {len(only_in_traversal)}")
    print()

    if only_in_direct:
        print("Sections only found via direct iteration (not reachable from start):")
        # Show first 20
        for idx in sorted(only_in_direct)[:20]:
            section = next(s for s in db.sections if s.index == idx)
            print(f"  Section {idx}: {len(section.nodes)} nodes, next={section.next_section}")
        if len(only_in_direct) > 20:
            print(f"  ... and {len(only_in_direct) - 20} more")
        print()

    if only_in_traversal:
        print("Sections only found via traversal (ERROR - should not happen):")
        for idx in sorted(only_in_traversal)[:20]:
            print(f"  Section {idx}")
        print()

    # Verdict
    print("=" * 60)
    if len(only_in_direct) == 0 and len(only_in_traversal) == 0:
        print("VERDICT: IDENTICAL")
        print("Direct iteration produces the same result as traversal.")
        print("Safe to replace traversal with direct iteration.")
    elif len(only_in_traversal) == 0:
        print("VERDICT: DIRECT ITERATION CAPTURES MORE")
        print(f"Traversal misses {len(only_in_direct)} sections (disconnected from start).")
        print("Direct iteration is MORE complete - safe to use.")
        print()
        print("NOTE: The 'missing' sections may be:")
        print("  - Disconnected track segments")
        print("  - Sections only reachable from a different starting point")
        print("  - Yard tracks or sidings not connected to main line")
    else:
        print("VERDICT: UNEXPECTED DIFFERENCE")
        print("Traversal found sections not in database - investigate!")
    print("=" * 60)


if __name__ == '__main__':
    main()
