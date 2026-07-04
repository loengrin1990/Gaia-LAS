from __future__ import annotations

import unittest

from gaia.config import ConfigError, config_version


class ConfigContractTests(unittest.TestCase):
    def test_missing_config_version_defaults_to_current_version(self) -> None:
        self.assertEqual(config_version({}), 1)

    def test_config_version_must_be_supported(self) -> None:
        with self.assertRaisesRegex(ConfigError, "Unsupported config_version 2"):
            config_version({"config_version": 2})

    def test_config_version_must_be_integer(self) -> None:
        with self.assertRaisesRegex(ConfigError, "must be an integer"):
            config_version({"config_version": "1"})


if __name__ == "__main__":
    unittest.main()
