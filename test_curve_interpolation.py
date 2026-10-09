import math
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from config_parser import TileBasedConfig
from region_extractor import (
    METERS_PER_DEGREE_LAT,
    extract_sections,
    interpolate_curve,
    interpolate_curve_geographic,
)


ALINE_START = (733.92, 29.32, -871.91)
ALINE_END = (740.57, 29.32, -443.96)
ALINE_RADIUS = 214.0
ALINE_ARC_LENGTH = 673.72
ALINE_DEGREES = 180.379959


def polyline_length(points):
    return sum(math.hypot(b[0] - a[0], b[1] - a[1])
               for a, b in zip(points, points[1:]))


def cosine_similarity(a, b):
    return ((a[0] * b[0] + a[1] * b[1]) /
            (math.hypot(*a) * math.hypot(*b)))


class CurveInterpolationTests(unittest.TestCase):
    def assert_points_almost_equal(self, left, right, places=9):
        self.assertEqual(len(left), len(right))
        for actual, expected in zip(left, right):
            self.assertAlmostEqual(actual[0], expected[0], places=places)
            self.assertAlmostEqual(actual[1], expected[1], places=places)

    def test_aline_6124_tile_extraction_uses_original_sign(self):
        node = type("Node", (), {})()
        node.__dict__.update(
            position=ALINE_START,
            end_position=ALINE_END,
            tile_index=(76, -282),
            radius_meters=ALINE_RADIUS,
            arcLen_meters=ALINE_ARC_LENGTH,
            curve_sign=1,
            curve_deg=ALINE_DEGREES,
            num_segments=336,
            is_reverse_path=False,
            is_switch_node=False,
        )
        section = SimpleNamespace(
            index=6124,
            nodes=[node],
            track_type=0,
            retarder_mph=-1.0,
            is_ctc_switch=False,
        )
        db = SimpleNamespace(sections=[section])
        tile_config = TileBasedConfig(
            home_tile=(76, -282), tile_width=842.3, tile_height=1023.2)

        with patch("region_extractor.compute_switch_leg_fixes", return_value=({}, [])):
            result = extract_sections(db, tile_based_config=tile_config)

        self.assertEqual(len(result), 1)
        path = result[0].paths[0]
        self.assertEqual(path[0], (ALINE_START[0], -ALINE_START[2]))
        self.assertEqual(path[-1], (ALINE_END[0], -ALINE_END[2]))
        self.assertGreater(path[len(path) // 2][0], max(ALINE_START[0], ALINE_END[0]))

    def test_aline_6124_geometry_and_tangents(self):
        points = interpolate_curve(
            ALINE_START, ALINE_END, ALINE_RADIUS, ALINE_ARC_LENGTH,
            curve_sign=1, num_segments=336, curve_degrees=ALINE_DEGREES)

        self.assertEqual(points[0], (ALINE_START[0], ALINE_START[2]))
        self.assertEqual(points[-1], (ALINE_END[0], ALINE_END[2]))
        self.assertTrue(all(math.isfinite(value) for point in points for value in point))
        self.assertGreater(points[len(points) // 2][0], max(ALINE_START[0], ALINE_END[0]))
        self.assertAlmostEqual(polyline_length(points), ALINE_ARC_LENGTH, delta=2.0)

        start_tangent = (points[1][0] - points[0][0],
                         points[1][1] - points[0][1])
        end_tangent = (points[-1][0] - points[-2][0],
                       points[-1][1] - points[-2][1])
        self.assertGreater(cosine_similarity(start_tangent, (1.0, 0.0)), 0.999)
        self.assertGreater(cosine_similarity(end_tangent, (-1.0, 0.0)), 0.999)

    def test_exact_semicircle_signs_are_mirrored(self):
        positive = interpolate_curve(
            (-10.0, 0.0, 0.0), (10.0, 0.0, 0.0), 10.0, math.pi * 10.0,
            curve_sign=1, num_segments=20, curve_degrees=180.0)
        negative = interpolate_curve(
            (-10.0, 0.0, 0.0), (10.0, 0.0, 0.0), 10.0, math.pi * 10.0,
            curve_sign=-1, num_segments=20, curve_degrees=180.0)

        for pos, neg in zip(positive, negative):
            self.assertAlmostEqual(pos[0], neg[0])
            self.assertAlmostEqual(pos[1], -neg[1])
        self.assertLess(positive[len(positive) // 2][1], 0.0)

    def test_near_180_does_not_mirror_when_degrees_cross_boundary(self):
        for sign in (-1, 1):
            below = interpolate_curve(
                (-10.0, 0.0, 0.0), (10.0, 0.0, 0.0), 10.0, math.pi * 10.0,
                curve_sign=sign, num_segments=20, curve_degrees=179.999)
            above = interpolate_curve(
                (-10.0, 0.0, 0.0), (10.0, 0.0, 0.0), 10.0, math.pi * 10.0,
                curve_sign=sign, num_segments=20, curve_degrees=180.001)
            negative_degrees = interpolate_curve(
                (-10.0, 0.0, 0.0), (10.0, 0.0, 0.0), 10.0, math.pi * 10.0,
                curve_sign=sign, num_segments=20, curve_degrees=-180.001)
            self.assert_points_almost_equal(below, above)
            self.assert_points_almost_equal(above, negative_degrees)

    def test_standard_short_and_long_arcs_keep_expected_sides(self):
        short_positive = interpolate_curve(
            (0.0, 0.0, 0.0), (10.0, 0.0, 10.0), 10.0, math.pi * 5.0,
            curve_sign=1, num_segments=2, curve_degrees=90.0)
        short_negative = interpolate_curve(
            (0.0, 0.0, 0.0), (10.0, 0.0, 10.0), 10.0, math.pi * 5.0,
            curve_sign=-1, num_segments=2, curve_degrees=90.0)
        self.assertAlmostEqual(short_positive[1][0], 7.071067812, places=8)
        self.assertAlmostEqual(short_positive[1][1], 2.928932188, places=8)
        self.assertAlmostEqual(short_negative[1][0], 2.928932188, places=8)
        self.assertAlmostEqual(short_negative[1][1], 7.071067812, places=8)

        chord = 20.0 * math.sin(math.radians(80.0))
        long_positive = interpolate_curve(
            (0.0, 0.0, 0.0), (chord, 0.0, 0.0), 10.0,
            math.radians(200.0) * 10.0, curve_sign=1,
            num_segments=2, curve_degrees=200.0)
        long_negative = interpolate_curve(
            (0.0, 0.0, 0.0), (chord, 0.0, 0.0), 10.0,
            math.radians(200.0) * 10.0, curve_sign=-1,
            num_segments=2, curve_degrees=200.0)
        self.assertLess(long_positive[1][1], 0.0)
        self.assertGreater(long_negative[1][1], 0.0)
        self.assertAlmostEqual(long_positive[1][0], long_negative[1][0])
        self.assertAlmostEqual(long_positive[1][1], -long_negative[1][1])

    def test_defensive_inputs_and_cross_tile_scale(self):
        start = (1.0, 2.0, 3.0)
        end = (4.0, 5.0, 6.0)
        self.assertEqual(interpolate_curve(start, end, 0.01, 1.0, 1),
                         [(1.0, 3.0), (4.0, 6.0)])
        self.assertEqual(interpolate_curve(start, start, 10.0, 1.0, 1),
                         [(1.0, 3.0), (1.0, 3.0)])
        self.assertEqual(
            interpolate_curve((0.0, 0.0, 0.0), (20.000000001, 0.0, 0.0),
                              10.0, 20.0, 1, curve_degrees=170.0),
            [(0.0, 0.0), (20.000000001, 0.0)])

        cross_tile = interpolate_curve(
            (-100.0, 0.0, -900.0), (900.0, 0.0, -1200.0), 600.0,
            1200.0, curve_sign=1, num_segments=50, curve_degrees=120.0)
        self.assertEqual(cross_tile[0], (-100.0, -900.0))
        self.assertEqual(cross_tile[-1], (900.0, -1200.0))
        self.assertTrue(all(math.isfinite(value) for point in cross_tile for value in point))

    def test_geographic_mode_preserves_coordinate_inversion(self):
        latitude = 34.0
        meters_per_degree_lon = METERS_PER_DEGREE_LAT * math.cos(math.radians(latitude))
        half_chord_lon = 10.0 / meters_per_degree_lon
        start = (latitude, -118.0 - half_chord_lon)
        end = (latitude, -118.0 + half_chord_lon)

        positive = interpolate_curve_geographic(
            start, end, 10.0, math.pi * 10.0, 1, 180.001, num_segments=20)
        negative = interpolate_curve_geographic(
            start, end, 10.0, math.pi * 10.0, -1, 180.001, num_segments=20)

        self.assert_points_almost_equal([positive[0], positive[-1]], [start, end])
        self.assert_points_almost_equal([negative[0], negative[-1]], [start, end])
        self.assertGreater(positive[len(positive) // 2][0], latitude)
        self.assertLess(negative[len(negative) // 2][0], latitude)


if __name__ == "__main__":
    unittest.main()
