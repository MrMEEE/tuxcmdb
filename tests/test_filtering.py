from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tuxcmdb-api"))

from filtering import And, FilterSyntaxError, Not, Or, Predicate, parse_filter


class ParseFilterTests(unittest.TestCase):
    def test_requested_expression(self) -> None:
        self.assertEqual(
            parse_filter("os=RHEL AND (os NOT RHEL9) AND ip_address=192.168."),
            And(
                And(Predicate("os", "RHEL"), Not(Predicate("os", "RHEL9"))),
                Predicate("ip_address", "192.168."),
            ),
        )

    def test_operator_precedence(self) -> None:
        self.assertEqual(
            parse_filter("a=1 OR b=2 AND NOT c=3"),
            Or(Predicate("a", "1"), And(Predicate("b", "2"), Not(Predicate("c", "3")))),
        )

    def test_quoted_values_and_escapes(self) -> None:
        self.assertEqual(parse_filter('os="RHEL \\"special\\""'), Predicate("os", 'RHEL "special"'))

    def test_keywords_are_case_insensitive(self) -> None:
        self.assertEqual(
            parse_filter("os=rhel and environment not dev"),
            And(Predicate("os", "rhel"), Not(Predicate("environment", "dev"))),
        )

    def test_reports_invalid_expression_position(self) -> None:
        with self.assertRaisesRegex(FilterSyntaxError, "Expected a filter value at position 3"):
            parse_filter("os=")

        with self.assertRaisesRegex(FilterSyntaxError, "Expected '\\)' at position 8"):
            parse_filter("(os=RHEL")


if __name__ == "__main__":
    unittest.main()