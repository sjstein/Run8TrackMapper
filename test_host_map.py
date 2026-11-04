import tempfile
import unittest
from pathlib import Path

from host_map import _create_telemetry_client
from serve import AuthoringState, parse_train_poll_ms


class HostMapTelemetryTests(unittest.TestCase):
    def test_omitted_url_preserves_world_save_only_mode(self):
        self.assertIsNone(_create_telemetry_client())

    def test_url_enables_telemetry_without_authentication(self):
        client = _create_telemetry_client("http://127.0.0.1:40872")

        self.assertEqual(client.base_url, "http://127.0.0.1:40872")
        self.assertIsNone(client.bearer_token)

    def test_token_is_loaded_from_file(self):
        with tempfile.TemporaryDirectory() as directory:
            token_file = Path(directory) / "telemetry-token"
            token_file.write_text("secret-token\n", encoding="utf-8")

            client = _create_telemetry_client(
                "http://127.0.0.1:40872", token_file)

        self.assertEqual(client.bearer_token, "secret-token")

    def test_missing_or_empty_token_file_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing-token"
            with self.assertRaisesRegex(ValueError, "cannot read telemetry token"):
                _create_telemetry_client("http://127.0.0.1:40872", missing)

            empty = Path(directory) / "empty-token"
            empty.write_text("\r\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "telemetry token file is empty"):
                _create_telemetry_client("http://127.0.0.1:40872", empty)


class TrainPollingTests(unittest.TestCase):
    def test_poll_interval_defaults_and_validation(self):
        self.assertEqual(parse_train_poll_ms(), 5000)
        self.assertEqual(parse_train_poll_ms('10000'), 10000)
        for invalid in ('fast', 999, 60001):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    parse_train_poll_ms(invalid)

    def test_response_is_serialized_and_compressed_once_per_interval(self):
        import gzip
        import json

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = AuthoringState(root / 'config.ini', root, root / 'areas.ini',
                                   train_poll_ms=5000)
            calls = []

            def fresh_payload():
                calls.append(1)
                state._trains_payload_revision += 1
                return {'version': len(calls), 'trains': {'socal': [{'id': 1}]}}

            state.trains_payload = fresh_payload
            plain, encoding = state.trains_response(False)
            zipped, zip_encoding = state.trains_response(True)

            self.assertEqual(len(calls), 1)
            self.assertIsNone(encoding)
            self.assertEqual(zip_encoding, 'gzip')
            self.assertEqual(gzip.decompress(zipped), plain)
            self.assertEqual(json.loads(plain)['version'], 1)

            # Expiring the shared frame permits exactly one new placement.
            state._trains_response_at -= 5.1
            refreshed, _ = state.trains_response(False)
            self.assertEqual(len(calls), 2)
            self.assertEqual(json.loads(refreshed)['version'], 2)


if __name__ == "__main__":
    unittest.main()
