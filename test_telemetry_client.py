import json
import unittest
from unittest.mock import patch

from telemetry_client import TelemetryClient, TelemetryUnavailable


def truck(index, section, distance=10.0):
    return {
        "truckIndex": index, "valid": True, "routePrefix": 140,
        "trackSectionIndex": section, "trackSectionCrossCheck": section,
        "startNodeIndex": 0, "distanceTravelledMeters": distance,
        "reverseDirection": False, "tileX": 0, "tileZ": 0,
        "localX": 0.0, "localY": 0.0, "localZ": 0.0,
    }


def vehicle(vehicle_id=100, consist_index=0, train_id=None, distance=10.0):
    value = {
        "vehicleId": vehicle_id, "consistIndex": consist_index,
        "roadName": "CSXT", "unitNumber": vehicle_id, "railUnitType": 3,
        "xmlFilename": "loco.xml", "destinationTag": "WAYX",
        "lengthMeters": 20.0, "loadWeightUsTons": 5.0,
        "trucks": [truck(0, 10, distance), truck(1, 11, distance + 5)],
    }
    if train_id is not None:
        value["trainId"] = train_id
    return value


def train(train_id=42, vehicles=None, speed=12.0):
    value = {
        "trainId": train_id, "symbol": "TEST", "leadUnit": "CSXT100",
        "trainType": 0, "isAi": False, "speedMph": speed,
        "consistFingerprint": "0000000000000001",
    }
    if vehicles is not None:
        value["vehicles"] = vehicles
    return value


def context(sim_time="2026-10-30T15:04:05.0000000"):
    return {"simulationTime": sim_time, "switches": {"3": [1], "140": [0, 4]}}


class Response:
    def __init__(self, value):
        self.body = json.dumps(value).encode("utf-8")
        self.status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size=-1):
        return self.body[:size]


class TelemetryClientTests(unittest.TestCase):
    def test_snapshot_delta_and_unchanged_cursor(self):
        health = {"run8Connected": True, "stale": False}
        snapshot = {
            "schemaVersion": 2, "sessionId": "11111111-1111-1111-1111-111111111111",
            "sequence": 10, "trains": [train(vehicles=[vehicle()])],
            **context(),
        }
        moved = vehicle(train_id=42, distance=25.0)
        changes = {
            "schemaVersion": 2, "sessionId": snapshot["sessionId"],
            "fromSequence": 10, "toSequence": 12, "resetRequired": False,
            "trainUpserts": [train(vehicles=None, speed=18.0)],
            "vehicleUpserts": [moved], "removedTrainIds": [], "removedVehicleIds": [],
            **context("2026-10-30T15:04:06.0000000"),
        }
        unchanged = dict(changes, fromSequence=12, toSequence=13,
                         trainUpserts=[], vehicleUpserts=[],
                         **context("2026-10-30T15:04:07.0000000"))
        responses = iter([health, snapshot, health, changes, health, unchanged])
        client = TelemetryClient("http://bridge", health_interval=0)
        with patch("urllib.request.urlopen", side_effect=lambda *_a, **_k: Response(next(responses))):
            self.assertTrue(client.refresh())
            self.assertEqual(client.world_trains()[0].vehicles[0].unit_type, "US_DieselEngine")
            self.assertEqual(client.simulation_time, "2026-10-30T15:04:05.0000000")
            self.assertEqual(client.switch_states, {3: [1], 140: [0, 4]})
            self.assertTrue(client.refresh())
            self.assertEqual(client.sequence, 12)
            self.assertEqual(client.world_trains()[0].vehicles[0].truck_a.distance_m, 25.0)
            self.assertTrue(client.refresh())
            self.assertEqual(client.sequence, 13)
            self.assertEqual(client.simulation_time, "2026-10-30T15:04:07.0000000")

    def test_reset_fetches_new_snapshot(self):
        health = {"run8Connected": True, "stale": False}
        first = {"schemaVersion": 1, "sessionId": "11111111-1111-1111-1111-111111111111",
                 "sequence": 1, "trains": [train(vehicles=[vehicle()])]}
        reset = {"schemaVersion": 1, "sessionId": first["sessionId"],
                 "fromSequence": 1, "toSequence": 2, "resetRequired": True,
                 "trainUpserts": [], "vehicleUpserts": [],
                 "removedTrainIds": [], "removedVehicleIds": []}
        second = {"schemaVersion": 1, "sessionId": "22222222-2222-2222-2222-222222222222",
                  "sequence": 3, "trains": [train(77, [vehicle(200)])]}
        responses = iter([health, first, health, reset, second])
        client = TelemetryClient("http://bridge", health_interval=0)
        with patch("urllib.request.urlopen", side_effect=lambda *_a, **_k: Response(next(responses))):
            client.refresh()
            self.assertTrue(client.refresh())
        self.assertEqual(client.session_id, second["sessionId"])
        self.assertEqual(client.world_trains()[0].train_id, 77)

    def test_stale_health_preserves_last_good_model(self):
        client = TelemetryClient("http://bridge", health_interval=0)
        client.session_id = "11111111-1111-1111-1111-111111111111"
        client.sequence = 1
        client.model_sequence = 1
        client._trains = {42: train(vehicles=None)}
        with patch("urllib.request.urlopen", return_value=Response(
                {"run8Connected": True, "stale": True})):
            with self.assertRaises(TelemetryUnavailable):
                client.refresh()
        self.assertEqual(client.world_trains()[0].train_id, 42)


if __name__ == "__main__":
    unittest.main()
