import unittest

from counter import total


class TestTotal(unittest.TestCase):
    def test_an_empty_basket_is_zero(self):
        self.assertEqual(total([]), 0)

    def test_it_adds_up(self):
        self.assertEqual(total([1, 2, 3]), 6)
