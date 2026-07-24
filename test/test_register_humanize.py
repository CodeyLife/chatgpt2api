from __future__ import annotations

import unittest

from services.register.humanize import Humanizer, from_runtime_config


class RegisterHumanizeTests(unittest.TestCase):
    def test_from_runtime_config_normalizes_enabled_factor_and_ranges(self) -> None:
        config = from_runtime_config({
            "humanize": {
                "enabled": "false",
                "factor": "0.5",
                "delays": {"otp_input": [2, 4], "form": "1~3"},
            }
        })

        self.assertFalse(config.enabled)
        self.assertEqual(config.factor, 0.5)
        self.assertEqual(config.delays["otp_input"], (2.0, 4.0))
        self.assertEqual(config.delays["form"], (1.0, 3.0))

    def test_humanizer_delay_uses_factor_and_sleep_func(self) -> None:
        slept: list[float] = []
        humanizer = Humanizer(
            {"enabled": True, "factor": 0.5, "delays": {"api": [2, 4]}},
            sleep_func=slept.append,
            random_func=lambda lo, hi: hi,
        )

        seconds = humanizer.delay("api")

        self.assertEqual(seconds, 2.0)
        self.assertEqual(slept, [2.0])

    def test_disabled_humanizer_does_not_sleep(self) -> None:
        slept: list[float] = []
        humanizer = Humanizer({"enabled": False}, sleep_func=slept.append)

        self.assertEqual(humanizer.delay("api"), 0.0)
        self.assertEqual(slept, [])


if __name__ == "__main__":
    unittest.main()
