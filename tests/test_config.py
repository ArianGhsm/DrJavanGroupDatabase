import os
from unittest import TestCase
from unittest.mock import patch

from drjavanbot.config import ConfigurationError, DEFAULT_AVALAI_BASE_URL, DEFAULT_AVALAI_MODEL, Settings


class SettingsTests(TestCase):
    def test_defaults_do_not_require_secrets_for_build_time(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings.from_env()
        self.assertIsNone(settings.telegram_bot_token)
        self.assertIsNone(settings.telegram_owner_id)
        self.assertEqual(settings.avalai_base_url, DEFAULT_AVALAI_BASE_URL)
        self.assertEqual(settings.avalai_model, DEFAULT_AVALAI_MODEL)

    def test_owner_must_be_numeric(self):
        with patch.dict(os.environ, {"TELEGRAM_OWNER_ID": "arian"}, clear=True):
            with self.assertRaises(ConfigurationError):
                Settings.from_env()

    def test_runtime_requires_token_and_owner(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ConfigurationError):
                Settings.from_env(require_runtime=True)
