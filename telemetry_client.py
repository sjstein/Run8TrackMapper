"""Client-side state for the Run8 telemetry bridge snapshot/delta API.

The mapper's placement pipeline consumes ``world_parser`` records.  This module
keeps the bridge protocol isolated from that pipeline by translating complete
telemetry vehicle records into the same Train/RailVehicle/Truck model.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, Optional

from world_parser import RailVehicle, Train, Truck


MAX_RESPONSE_BYTES = 64 * 1024 * 1024
RAIL_UNIT_TYPES = (
    "Unknown",
    "US_Freightcar",
    "US_Passengercar",
    "US_DieselEngine",
    "US_SteamEngine",
    "US_ElectricEngine",
    "UK_Freightcar",
    "UK_Passengercar",
    "UK_DieselEngine",
    "UK_SteamEngine",
    "UK_ElectricEngine",
)


class TelemetryUnavailable(RuntimeError):
    """The bridge is unreachable, stale, disconnected, or returned bad data."""


class TelemetryClient:
    """Maintain an authoritative train model from bridge snapshots and deltas."""

    def __init__(self, base_url: str, bearer_token: Optional[str] = None,
                 timeout: float = 3.0, health_interval: float = 5.0):
        self.base_url = base_url.rstrip("/")
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError("telemetry base URL must use http:// or https://")
        self.bearer_token = (bearer_token or "").strip() or None
        self.timeout = timeout
        self.health_interval = health_interval
        self.session_id = None
        self.sequence = None
        self.model_sequence = None
        self.schema_version = None
        self.simulation_time = None
        self.switch_states: Dict[int, list] = {}
        self.context_available = False
        self._trains: Dict[int, dict] = {}
        self._vehicles: Dict[int, dict] = {}
        self._last_health_check = 0.0

    @property
    def ready(self) -> bool:
        return self.session_id is not None and self.sequence is not None

    @property
    def version(self) -> str:
        if not self.ready:
            return "telemetry:uninitialized"
        return "telemetry:{}:{}".format(self.session_id, self.model_sequence)

    @property
    def train_speeds(self) -> Dict[int, float]:
        return {train_id: float(train.get("speedMph", 0.0))
                for train_id, train in self._trains.items()}

    def refresh(self) -> bool:
        """Fetch new state. Return True only when the train model changed."""
        now = time.monotonic()
        if not self.ready or now - self._last_health_check >= self.health_interval:
            health = self._get_json("/healthz")
            if not health.get("run8Connected"):
                raise TelemetryUnavailable("Run8 telemetry producer is disconnected")
            if health.get("stale"):
                raise TelemetryUnavailable("Run8 telemetry snapshot is stale")
            self._last_health_check = now

        if not self.ready:
            self._load_snapshot()
            return True

        query = urllib.parse.urlencode({
            "since": self.sequence,
            "sessionId": self.session_id,
        })
        changes = self._get_json("/v1/changes?" + query)
        if changes.get("resetRequired"):
            self._load_snapshot()
            return True
        return self._apply_changes(changes)

    def world_trains(self):
        """Return the current model as world_parser.Train records."""
        grouped = {train_id: [] for train_id in self._trains}
        for vehicle in self._vehicles.values():
            train_id = int(vehicle["trainId"])
            if train_id in grouped:
                grouped[train_id].append(vehicle)

        result = []
        for train_id in sorted(self._trains):
            metadata = self._trains[train_id]
            vehicles = sorted(grouped[train_id], key=lambda value: int(value["consistIndex"]))
            result.append(Train(
                train_id=train_id,
                was_ai=bool(metadata.get("isAi", False)),
                vehicles=[self._world_vehicle(value) for value in vehicles],
            ))
        return result

    def _load_snapshot(self):
        snapshot = self._get_json("/v1/snapshot")
        schema_version = snapshot.get("schemaVersion")
        if schema_version not in (1, 2):
            raise TelemetryUnavailable("unsupported telemetry snapshot schema")
        session_id = snapshot.get("sessionId")
        sequence = snapshot.get("sequence")
        raw_trains = snapshot.get("trains")
        if not isinstance(session_id, str) or not isinstance(sequence, int) or not isinstance(raw_trains, list):
            raise TelemetryUnavailable("invalid telemetry snapshot envelope")

        trains = {}
        vehicles = {}
        for raw_train in raw_trains:
            train = dict(raw_train)
            raw_vehicles = train.pop("vehicles", None)
            train_id = train.get("trainId")
            if not isinstance(train_id, int) or not isinstance(raw_vehicles, list) or train_id in trains:
                raise TelemetryUnavailable("invalid or duplicate telemetry train")
            trains[train_id] = train
            for raw_vehicle in raw_vehicles:
                vehicle = dict(raw_vehicle)
                vehicle["trainId"] = train_id
                vehicle_id = vehicle.get("vehicleId")
                if not isinstance(vehicle_id, int) or vehicle_id in vehicles:
                    raise TelemetryUnavailable("invalid or duplicate telemetry vehicle")
                vehicles[vehicle_id] = vehicle
        self._validate_model(trains, vehicles)
        simulation_time, switch_states = self._read_context(snapshot, schema_version)
        self._trains = trains
        self._vehicles = vehicles
        self.schema_version = schema_version
        self.simulation_time = simulation_time
        self.switch_states = switch_states
        self.context_available = schema_version >= 2
        self.session_id = session_id
        self.sequence = sequence
        self.model_sequence = sequence

    def _apply_changes(self, changes: dict) -> bool:
        if (changes.get("schemaVersion") != self.schema_version or
                changes.get("sessionId") != self.session_id):
            raise TelemetryUnavailable("telemetry delta session/schema mismatch")
        if changes.get("fromSequence") != self.sequence:
            raise TelemetryUnavailable("telemetry delta did not echo the requested cursor")
        to_sequence = changes.get("toSequence")
        if not isinstance(to_sequence, int) or to_sequence < self.sequence:
            raise TelemetryUnavailable("telemetry delta sequence moved backwards")

        train_upserts = changes.get("trainUpserts")
        vehicle_upserts = changes.get("vehicleUpserts")
        removed_trains = changes.get("removedTrainIds")
        removed_vehicles = changes.get("removedVehicleIds")
        if not all(isinstance(value, list) for value in
                   (train_upserts, vehicle_upserts, removed_trains, removed_vehicles)):
            raise TelemetryUnavailable("invalid telemetry delta collections")

        simulation_time, switch_states = self._read_context(changes, self.schema_version)
        context_changed = (simulation_time != self.simulation_time or
                           switch_states != self.switch_states)
        changed = bool(train_upserts or vehicle_upserts or removed_trains or removed_vehicles or
                       context_changed)
        if changed:
            trains = dict(self._trains)
            vehicles = dict(self._vehicles)
            for train_id in removed_trains:
                trains.pop(int(train_id), None)
            for vehicle_id in removed_vehicles:
                vehicles.pop(int(vehicle_id), None)
            for raw_train in train_upserts:
                train = dict(raw_train)
                trains[int(train["trainId"])] = train
            for raw_vehicle in vehicle_upserts:
                vehicle = dict(raw_vehicle)
                vehicles[int(vehicle["vehicleId"])] = vehicle
            self._validate_model(trains, vehicles)
            self._trains = trains
            self._vehicles = vehicles
            self.model_sequence = to_sequence
        self.simulation_time = simulation_time
        self.switch_states = switch_states
        self.sequence = to_sequence
        return changed

    @staticmethod
    def _read_context(envelope: dict, schema_version: int):
        if schema_version < 2:
            return None, {}
        simulation_time = envelope.get("simulationTime")
        raw_switches = envelope.get("switches")
        if not isinstance(simulation_time, str) or not simulation_time or not isinstance(raw_switches, dict):
            raise TelemetryUnavailable("invalid telemetry clock/switch context")
        switch_states = {}
        for raw_prefix, raw_indexes in raw_switches.items():
            try:
                prefix = int(raw_prefix)
            except (TypeError, ValueError) as exc:
                raise TelemetryUnavailable("invalid telemetry switch route prefix") from exc
            if (not isinstance(raw_indexes, list) or
                    any(not isinstance(value, int) or value < 0 for value in raw_indexes) or
                    raw_indexes != sorted(set(raw_indexes))):
                raise TelemetryUnavailable("invalid telemetry switch indexes")
            switch_states[prefix] = list(raw_indexes)
        return simulation_time, switch_states

    @staticmethod
    def _validate_model(trains: Dict[int, dict], vehicles: Dict[int, dict]):
        indexes = {train_id: [] for train_id in trains}
        for vehicle_id, vehicle in vehicles.items():
            train_id = vehicle.get("trainId")
            if train_id not in trains:
                raise TelemetryUnavailable(
                    "telemetry vehicle {} references missing train {}".format(vehicle_id, train_id))
            consist_index = vehicle.get("consistIndex")
            trucks = vehicle.get("trucks")
            if not isinstance(consist_index, int) or not isinstance(trucks, list) or len(trucks) != 2:
                raise TelemetryUnavailable("invalid telemetry vehicle record")
            indexes[train_id].append(consist_index)
        for train_id, values in indexes.items():
            if sorted(values) != list(range(len(values))):
                raise TelemetryUnavailable(
                    "telemetry train {} has non-contiguous consist indexes".format(train_id))

    @staticmethod
    def _world_vehicle(vehicle: dict) -> RailVehicle:
        trucks = vehicle["trucks"]
        unit_type = int(vehicle.get("railUnitType", 0))
        unit_type_name = (RAIL_UNIT_TYPES[unit_type]
                          if 0 <= unit_type < len(RAIL_UNIT_TYPES)
                          else "RailUnitType_{}".format(unit_type))
        return RailVehicle(
            rv_filename=str(vehicle.get("xmlFilename", "")),
            unit_type=unit_type_name,
            unit_number=str(vehicle.get("unitNumber", "")),
            destination_tag=str(vehicle.get("destinationTag", "")),
            truck_a=TelemetryClient._world_truck(trucks[0]),
            truck_b=TelemetryClient._world_truck(trucks[1]),
            load_tons=float(vehicle.get("loadWeightUsTons", 0.0)),
        )

    @staticmethod
    def _world_truck(truck: dict) -> Truck:
        valid = bool(truck.get("valid", False))
        return Truck(
            section_index=int(truck.get("trackSectionIndex", -1)) if valid else -1,
            start_node_index=int(truck.get("startNodeIndex", -1)) if valid else -1,
            distance_m=float(truck.get("distanceTravelledMeters", 0.0)),
            route_prefix=int(truck.get("routePrefix", -1)) if valid else -1,
            reverse=bool(truck.get("reverseDirection", False)),
        )

    def _get_json(self, path: str) -> dict:
        request = urllib.request.Request(self.base_url + path, headers={"Cache-Control": "no-cache"})
        if self.bearer_token:
            request.add_header("Authorization", "Bearer " + self.bearer_token)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise TelemetryUnavailable("telemetry response exceeds size limit")
                if response.status != 200:
                    raise TelemetryUnavailable("telemetry HTTP status {}".format(response.status))
        except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
            raise TelemetryUnavailable("telemetry request failed: {}".format(exc)) from exc
        try:
            value = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TelemetryUnavailable("telemetry response is not valid JSON") from exc
        if not isinstance(value, dict):
            raise TelemetryUnavailable("telemetry response root is not an object")
        return value
