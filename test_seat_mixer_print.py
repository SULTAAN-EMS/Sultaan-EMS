from datetime import datetime, timezone
import unittest

from app.routes_seat_mixer import format_seat_plan_date


class TestSeatMixerPrintDate(unittest.TestCase):
    def test_seat_plan_date_uses_somali_names(self):
        self.assertEqual(
            format_seat_plan_date(datetime(2026, 10, 21)),
            "Arbaca, Oktoober 21, 2026",
        )

    def test_aware_seat_plan_date_is_shown_in_mogadishu_time(self):
        utc_date = datetime(2026, 10, 20, 22, 0, tzinfo=timezone.utc)
        self.assertEqual(
            format_seat_plan_date(utc_date),
            "Arbaca, Oktoober 21, 2026",
        )


if __name__ == "__main__":
    unittest.main()
