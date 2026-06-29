from __future__ import annotations

import unittest

from services.timezone import beijing_from_timestamp_string


class BeijingTimezoneTests(unittest.TestCase):
    def test_formats_timestamp_as_beijing_time(self) -> None:
        self.assertEqual(beijing_from_timestamp_string(0), "1970-01-01 08:00:00")


if __name__ == "__main__":
    unittest.main()
