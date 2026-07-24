from __future__ import annotations

import unittest
from datetime import date

from services.register.profile_utils import generate_random_birthday
from services.register_service import _normalize


class RegisterProfileUtilsTests(unittest.TestCase):
    def test_generate_random_birthday_uses_age_range(self) -> None:
        birthday = generate_random_birthday(
            min_age=18,
            max_age=18,
            today=date(2026, 7, 23),
            randint=lambda lo, hi: 0,
        )

        self.assertEqual(birthday, "2008-07-23")

    def test_generate_random_birthday_handles_leap_day(self) -> None:
        birthday = generate_random_birthday(
            min_age=18,
            max_age=18,
            today=date(2024, 2, 29),
            randint=lambda lo, hi: 0,
        )

        self.assertEqual(birthday, "2006-02-28")

    def test_normalize_profile_age_range(self) -> None:
        config = _normalize({"profile": {"min_age": "12", "max_age": "10"}})

        self.assertEqual(config["profile"]["min_age"], 13)
        self.assertEqual(config["profile"]["max_age"], 13)


if __name__ == "__main__":
    unittest.main()
