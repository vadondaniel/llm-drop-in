from __future__ import annotations

import unittest

from ui.formatting import format_bytes


class ByteFormattingTests(unittest.TestCase):
    def test_bytes_stay_in_bytes(self):
        self.assertEqual(format_bytes(0), "0 B")
        self.assertEqual(format_bytes(512), "512 B")
        self.assertEqual(format_bytes(1023), "1023 B")

    def test_kilobytes_are_used_for_small_archives(self):
        self.assertEqual(format_bytes(1024), "1 KB")
        self.assertEqual(format_bytes(52 * 1024), "52 KB")
        self.assertEqual(format_bytes(52 * 1024 + 512), "52.5 KB")

    def test_megabytes_are_used_once_the_threshold_is_reached(self):
        self.assertEqual(format_bytes(1024 * 1024), "1 MB")
        self.assertEqual(format_bytes(5 * 1024 * 1024 + 512 * 1024), "5.5 MB")

    def test_large_values_scale_up_to_gigabytes(self):
        self.assertEqual(format_bytes(1024**3), "1 GB")
        self.assertEqual(format_bytes(int(1.5 * 1024**3)), "1.5 GB")

    def test_trailing_zeroes_are_removed(self):
        self.assertEqual(format_bytes(2 * 1024 * 1024), "2 MB")

    def test_negative_sizes_are_rejected(self):
        with self.assertRaises(ValueError):
            format_bytes(-1)


if __name__ == "__main__":
    unittest.main()
