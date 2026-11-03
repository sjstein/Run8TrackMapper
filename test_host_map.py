import tempfile
import unittest
from pathlib import Path

from host_map import _create_telemetry_client


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


if __name__ == "__main__":
    unittest.main()
