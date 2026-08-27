import unittest

from app.services import calculate_score_totals


class TestDynamicResultMaximums(unittest.TestCase):
    def test_non_100_subject_maxima(self):
        total, maximum, percentage = calculate_score_totals(
            [(5, 9), (5, 9), (6.7, 9), (4, 9), (8.1, 9)]
        )
        self.assertEqual(float(total), 28.8)
        self.assertEqual(float(maximum), 45.0)
        self.assertEqual(percentage, 64.0)

    def test_mixed_subject_maxima(self):
        total, maximum, percentage = calculate_score_totals(
            [(8, 10), (17, 20), (37, 40), (78, 100)]
        )
        self.assertEqual(float(total), 140.0)
        self.assertEqual(float(maximum), 170.0)
        self.assertEqual(percentage, 82.35)

    def test_standard_100_maxima(self):
        total, maximum, percentage = calculate_score_totals(
            [(78, 100), (85, 100), (71, 100), (90, 100)]
        )
        self.assertEqual(float(total), 324.0)
        self.assertEqual(float(maximum), 400.0)
        self.assertEqual(percentage, 81.0)


if __name__ == "__main__":
    unittest.main()
