import unittest

from html_generator import generate_javascript


class SearchAndFollowQolTests(unittest.TestCase):
    def setUp(self):
        self.js = generate_javascript()

    def test_last_search_is_saved_restored_and_not_cleared_on_close(self):
        self.assertIn('st.search = {', self.js)
        self.assertIn("input.value = st.search.query", self.js)
        self.assertIn('if (input.value) performSearch()', self.js)
        close_body = self.js.split('function closeSearch()', 1)[1].split('function performSearch()', 1)[0]
        self.assertNotIn("search-input').value = ''", close_body)

    def test_followed_train_uses_yellow_for_detailed_and_collapsed_views(self):
        self.assertIn("const FOLLOW_COLOR = '#ffd400'", self.js)
        self.assertIn('const followed = train.train_id === MapApp.followTrainId', self.js)
        self.assertIn('followed ? FOLLOW_COLOR : rvBodyColor(v, true)', self.js)
        self.assertIn('followed ? FOLLOW_COLOR : rvBodyColor(v, false)', self.js)
        self.assertIn('followed ? FOLLOW_COLOR : (highlight ? PLAYER_HL_COLOR : COLLAPSED_BODY_COLOR)', self.js)

    def test_follow_start_and_stop_rerender_trains(self):
        follow_body = self.js.split('MapApp.followTrain = function', 1)[1].split('MapApp.stopFollow = function', 1)[0]
        stop_body = self.js.split('MapApp.stopFollow = function', 1)[1].split('function unloadRegion', 1)[0]
        self.assertIn("applyTrainOptions", follow_body)
        self.assertIn("applyTrainOptions", stop_body)


if __name__ == '__main__':
    unittest.main()
