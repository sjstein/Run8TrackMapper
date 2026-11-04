import unittest

from html_generator import ALIGN_JS, generate_javascript


class UiAdjustmentTests(unittest.TestCase):
    def setUp(self):
        self.js = generate_javascript()

    def test_display_controls_are_inside_main_panel(self):
        self.assertIn('<h4>Display</h4>', self.js)
        self.assertIn('id="display-settings"', self.js)
        self.assertIn("document.getElementById('display-settings').prepend(control)", self.js)

    def test_train_summary_toggle_defaults_on_and_is_persisted(self):
        self.assertIn('id="show-train-summary" checked', self.js)
        self.assertIn('showTrainSummary: true', self.js)
        self.assertIn('st.trainSummary = !!sum.checked', self.js)
        self.assertIn('!MapApp.showTrainSummary', self.js)

    def test_compact_zoom_and_help_controls(self):
        self.assertIn('.leaflet-control-zoom a', self.js)
        self.assertIn('width: 15px;', self.js)
        self.assertIn("help.textContent='?'", ALIGN_JS)
        self.assertIn('width:17px;height:17px', ALIGN_JS)

    def test_live_train_polling_is_completion_driven_and_non_overlapping(self):
        self.assertIn('Number(j.train_poll_ms) || 5000', ALIGN_JS)
        self.assertIn('if (MapApp._trainsRequest) return MapApp._trainsRequest', ALIGN_JS)
        self.assertIn('pollTrainsOnce().finally(scheduleNextTrainsPoll)', ALIGN_JS)
        self.assertNotIn('setInterval(pollTrainsOnce', ALIGN_JS)


if __name__ == '__main__':
    unittest.main()
