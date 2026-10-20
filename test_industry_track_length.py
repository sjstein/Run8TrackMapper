import unittest

from html_generator import generate_javascript
from output_generator import industry_to_dict
from region_extractor import IndustryData


class IndustryTrackLengthTests(unittest.TestCase):
    def test_industry_serialization_includes_track_length(self):
        industry = IndustryData(
            tag='ADC', name='Example', local_name='L-CAL0101',
            lat=1.0, lon=2.0, track_sections=[10, 11],
            track_length_ft=1234.5, track_length_m=376.3,
        )

        result = industry_to_dict(industry)

        self.assertEqual(1234.5, result['track_length_ft'])
        self.assertEqual(376.3, result['track_length_m'])

    def test_popup_places_track_length_between_local_and_sections(self):
        js = generate_javascript()
        local = js.index("Local: ${industry.local_name || 'N/A'}")
        length = js.index('Track Length: ${(industry.track_length_ft || 0).toFixed(1)}')
        sections = js.index('Track Sections: ${industry.track_sections.join', length)
        self.assertLess(local, length)
        self.assertLess(length, sections)


if __name__ == '__main__':
    unittest.main()
