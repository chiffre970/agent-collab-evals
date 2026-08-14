from __future__ import annotations

import unittest

from agent_collab_evals.canonical import (
    CanonicalizationError,
    DuplicateKeyError,
    canonical_json_bytes,
    digest_value,
    parse_json,
)


class CanonicalizationTests(unittest.TestCase):
    def test_mapping_order_does_not_change_digest(self) -> None:
        left = {"b": [2, 3], "a": {"value": 1}}
        right = {"a": {"value": 1}, "b": [2, 3]}

        self.assertEqual(canonical_json_bytes(left), canonical_json_bytes(right))
        self.assertEqual(digest_value(left), digest_value(right))

    def test_float_is_rejected_at_experiment_boundaries(self) -> None:
        with self.assertRaises(CanonicalizationError):
            canonical_json_bytes({"cost": 0.1})

    def test_duplicate_json_keys_are_rejected(self) -> None:
        with self.assertRaises(DuplicateKeyError):
            parse_json('{"model":"first","model":"second"}')


if __name__ == "__main__":
    unittest.main()
