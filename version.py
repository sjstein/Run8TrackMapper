#!/usr/bin/env python3
"""Single source of truth for the Run8 Map self-host bundle version.

Bumped on each released bundle. `host_map.py` reports this at startup and
compares it against the latest published version so an operator running an old
bundle is told a newer one exists (see host_map.check_for_update)."""

__version__ = "0.2.2"
