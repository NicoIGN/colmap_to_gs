import unittest

import numpy as np

from tools.clean_gs.clean_sky import rejection_mask


class VoteTest(unittest.TestCase):
    def test_sky_votes_and_foreground_override(self):
        sky = np.array([0, 1, 2, 3, 5])
        foreground = np.array([0, 0, 0, 1, 0])
        np.testing.assert_array_equal(
            rejection_mask(sky, foreground, 2), [False, False, True, False, True],
        )


if __name__ == "__main__":
    unittest.main()
