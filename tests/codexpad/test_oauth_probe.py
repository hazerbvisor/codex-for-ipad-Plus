import importlib.util
from pathlib import Path
import unittest
import urllib.parse

PROJECT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('oauth_probe', PROJECT / 'scripts/probe-codex-oauth.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class OAuthProbeTests(unittest.TestCase):
    def url(self, redirect):
        return 'https://auth.example.invalid/authorize?' + urllib.parse.urlencode(
            dict(redirect_uri=redirect, state='synthetic-state'))

    def test_only_invalid_code_is_sent_to_local_callback(self):
        result = urllib.parse.urlsplit(probe.callback_url(self.url('http://localhost:1455/auth/callback')))
        self.assertEqual(result.netloc, 'localhost:1455')
        self.assertEqual(urllib.parse.parse_qs(result.query),
                         {'state': ['synthetic-state'], 'code': ['codexpad-invalid-regression-code']})

    def test_remote_or_secret_bearing_redirect_is_rejected(self):
        for redirect in ('https://example.invalid/auth/callback', 'http://example.invalid:1455/auth/callback',
                         'http://localhost:4500/auth/callback', 'http://localhost:1455/other',
                         'http://user:password@localhost:1455/auth/callback',
                         'http://localhost:1455/auth/callback?private=value'):
            with self.subTest(redirect=redirect), self.assertRaises(probe.diagnostic.ProbeFailure):
                probe.callback_url(self.url(redirect))


if __name__ == '__main__':
    unittest.main()
