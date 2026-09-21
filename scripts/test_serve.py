"""Deployment binding must fail closed, independently of the web framework."""
import unittest
from unittest.mock import patch
from serve import validate_host, resolve_host

class PrivateBindTests(unittest.TestCase):
    def test_public_and_wildcard_binds_are_rejected(self):
        for host in ['0.0.0.0','::','8.8.8.8','192.168.1.2','example.com','localhost']:
            with self.subTest(host=host),self.assertRaises(ValueError):validate_host(host)
    def test_tailnet_and_explicit_loopback_test_bind(self):
        self.assertEqual(validate_host('100.64.0.1'),'100.64.0.1')
        with self.assertRaises(ValueError):validate_host('127.0.0.1')
        self.assertEqual(validate_host('127.0.0.1',allow_loopback=True),'127.0.0.1')
    def test_missing_tailscale_never_falls_back_to_public_or_loopback(self):
        with patch('serve.shutil.which',return_value=None),self.assertRaises(RuntimeError):resolve_host()
    def test_tailscale_discovery_is_validated(self):
        with patch('serve.shutil.which',return_value='/test/tailscale'),patch('serve.subprocess.check_output',return_value='0.0.0.0\n'),self.assertRaises(ValueError):resolve_host()

if __name__=='__main__':unittest.main()
