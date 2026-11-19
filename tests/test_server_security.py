import unittest

import server


class HostedAuthTests(unittest.TestCase):
    def test_loopback_development_does_not_require_password(self):
        self.assertIsNone(server.hosted_password_error("", ""))
        self.assertIsNone(server.hosted_password_error("   ", ""))

    def test_platform_port_requires_minimum_shared_password(self):
        self.assertIsNotNone(server.hosted_password_error("10000", ""))
        self.assertIsNotNone(server.hosted_password_error("10000", "short"))
        self.assertIsNone(server.hosted_password_error("10000", "correct-horse-123"))


if __name__ == "__main__":
    unittest.main()
