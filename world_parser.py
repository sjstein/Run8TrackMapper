#!/usr/bin/env python3
"""
Run8 world-save (.xml) parser for Run8 Track Mapper.

A Run8 world save (``<ScnLoader>``) stores every train under
``//trainList/TrainLoader`` and every rail vehicle under that loader's
``unitLoaderList/RailVehicleStateClass``. Each rail vehicle records two
*trucks* (front and rear); the paired position elements list truck A as the
first child and truck B as the second child, in this order:

    currentRoutePrefix        -> route filter (per truck)
    currentTrackSectionIndex  -> Run8 track-section number occupied by the truck
    startNodeIndex            -> which end of that section the distance is from
    distanceTravelledInMeters -> distance along the section from that end
    reverseDirection          -> truck facing (captured to show loco direction)

plus per-vehicle ``rvXMLfilename``, ``unitType``, ``destinationTag`` and
``unitNumber``.

This module only reads the data; converting a truck's ``(section, start node,
distance)`` into a map coordinate lives in :mod:`region_extractor` so it can
reuse the section geometry and the active coordinate mode. See
``world-import-rail-vehicle-ordering.md`` for the modelling background.

Parsing is deliberately tolerant: a missing or malformed int/float/bool becomes
0 / 0.0 / False, and floats are read with invariant (``.``) formatting, matching
how Run8 itself round-trips these saves.
"""

from dataclasses import dataclass, field
from typing import List, Optional
import xml.etree.ElementTree as ET


def _to_int(text: Optional[str]) -> int:
    """Parse an int, returning 0 on missing/garbage input."""
    if text is None:
        return 0
    try:
        return int(text.strip())
    except (ValueError, AttributeError):
        return 0


def _to_float(text: Optional[str]) -> float:
    """Parse an invariant-culture float, returning 0.0 on missing/garbage input."""
    if text is None:
        return 0.0
    try:
        return float(text.strip())
    except (ValueError, AttributeError):
        return 0.0


def _to_bool(text: Optional[str]) -> bool:
    """Parse an XML boolean ('true'/'false'), returning False on anything else."""
    return (text or "").strip().lower() == "true"


def _pair(elem: Optional[ET.Element], tag: str):
    """Return the two child text values of ``elem/<tag>`` as (first, second).

    The paired elements hold exactly two children (``<int>``/``<float>``/
    ``<boolean>``), one per truck. Missing children come back as ``None`` so the
    caller's ``_to_*`` helpers supply the default.
    """
    if elem is None:
        return (None, None)
    node = elem.find(tag)
    if node is None:
        return (None, None)
    children = list(node)
    first = children[0].text if len(children) >= 1 else None
    second = children[1].text if len(children) >= 2 else None
    return (first, second)


@dataclass
class Truck:
    """One truck's reported position (front = A, rear = B)."""
    section_index: int
    start_node_index: int
    distance_m: float
    route_prefix: int
    reverse: bool = False   # reverseDirection: the vehicle's facing (used to show loco direction)


@dataclass
class RailVehicle:
    """A single rail vehicle (locomotive or car) with its two trucks."""
    rv_filename: str
    unit_type: str
    unit_number: str
    destination_tag: str
    truck_a: Truck
    truck_b: Truck

    @property
    def route_prefix(self) -> int:
        """Route prefix used to assign the vehicle to a region (truck A wins)."""
        return self.truck_a.route_prefix


@dataclass
class Train:
    """A TrainLoader record: an ordered consist of rail vehicles."""
    train_id: int
    was_ai: bool
    vehicles: List[RailVehicle] = field(default_factory=list)


def _parse_vehicle(rv: ET.Element) -> RailVehicle:
    """Build a :class:`RailVehicle` from one ``<RailVehicleStateClass>``."""
    rp_a, rp_b = _pair(rv, "currentRoutePrefix")
    sec_a, sec_b = _pair(rv, "currentTrackSectionIndex")
    sn_a, sn_b = _pair(rv, "startNodeIndex")
    d_a, d_b = _pair(rv, "distanceTravelledInMeters")
    rev_a, rev_b = _pair(rv, "reverseDirection")

    truck_a = Truck(
        section_index=_to_int(sec_a),
        start_node_index=_to_int(sn_a),
        distance_m=_to_float(d_a),
        route_prefix=_to_int(rp_a),
        reverse=_to_bool(rev_a),
    )
    truck_b = Truck(
        section_index=_to_int(sec_b),
        start_node_index=_to_int(sn_b),
        distance_m=_to_float(d_b),
        route_prefix=_to_int(rp_b),
        reverse=_to_bool(rev_b),
    )

    def _text(tag: str) -> str:
        node = rv.find(tag)
        return (node.text or "").strip() if node is not None else ""

    return RailVehicle(
        rv_filename=_text("rvXMLfilename"),
        unit_type=_text("unitType"),
        unit_number=_text("unitNumber"),
        destination_tag=_text("destinationTag"),
        truck_a=truck_a,
        truck_b=truck_b,
    )


def parse_world_save(path: str) -> List[Train]:
    """Parse a Run8 world save into a list of :class:`Train`.

    Preserves the XML consist order of both trains and vehicles. Placement and
    ordering along the track are computed later from the section geometry.
    """
    tree = ET.parse(path)
    root = tree.getroot()

    trains: List[Train] = []
    for loader in root.findall(".//trainList/TrainLoader"):
        train_id = _to_int(loader.findtext("trainID"))
        was_ai = _to_bool(loader.findtext("TrainWasAI"))

        vehicles = [
            _parse_vehicle(rv)
            for rv in loader.findall("unitLoaderList/RailVehicleStateClass")
        ]
        trains.append(Train(train_id=train_id, was_ai=was_ai, vehicles=vehicles))

    return trains


def parse_sim_time(path: str) -> Optional[str]:
    """Return the world save's simulation clock - the top-level ``<date>`` element
    (an ISO-8601 timestamp), or ``None`` if absent / unparseable."""
    try:
        root = ET.parse(path).getroot()
    except Exception:  # noqa: BLE001 - a torn/missing save just has no sim time
        return None
    text = root.findtext("date")
    return text.strip() if text and text.strip() else None


if __name__ == "__main__":
    import sys

    src = sys.argv[1] if len(sys.argv) > 1 else "small_world.xml"
    parsed = parse_world_save(src)
    print(f"Parsed {len(parsed)} train(s) from {src}")
    for tr in parsed:
        print(f"  Train {tr.train_id} (AI={tr.was_ai}): {len(tr.vehicles)} vehicle(s)")
        for v in tr.vehicles:
            a, b = v.truck_a, v.truck_b
            print(f"    #{v.unit_number:>6} {v.unit_type:<16} dest={v.destination_tag:<6} "
                  f"A[sec {a.section_index} sn {a.start_node_index} {a.distance_m:.1f}m] "
                  f"B[sec {b.section_index} sn {b.start_node_index} {b.distance_m:.1f}m]")
