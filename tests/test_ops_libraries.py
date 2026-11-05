"""The cleaning steps built on open-source libraries: phonenumbers, email-validator, dateparser, RapidFuzz.
Offline; made-up data."""

import unittest

import pandas as pd

from cleaner_agent import common
from cleaner_agent.cleaner import ops


def run(op, values, **args):
    df = pd.DataFrame({"c": values})
    out, stats = getattr(ops, op)(df, {"op": op, "columns": ["c"], **args})
    return list(out["c"]), stats


class Phone(unittest.TestCase):
    def test_us_numbers_in_every_format(self):
        got, stats = run("standardize_phone", ["2015550123", "+1 (201) 555-0123", "201.555.0123", "1-201-555-0123"])
        self.assertEqual(set(got), {"(201) 555-0123"})
        self.assertEqual(stats["unparsed"], 0)

    def test_other_countries_and_formats(self):
        got, _ = run("standardize_phone", ["+44 20 7946 0958", "020 7946 0958"], country="GB", format="e164")
        self.assertEqual(got, ["+442079460958", "+442079460958"])
        got, _ = run("standardize_phone", ["+44 20 7946 0958"], country="US")
        self.assertEqual(got, ["+44 20 7946 0958"])  # foreign number: international layout

    def test_impossible_numbers_and_words_stay_as_they_were(self):
        got, stats = run("standardize_phone", ["12345", "call me", "+1 201 555 01"])
        self.assertEqual(got, ["12345", "call me", "+1 201 555 01"])
        self.assertEqual(stats["unparsed"], 3)

    def test_unassigned_numbers_are_formatted_and_counted(self):
        got, stats = run("standardize_phone", ["555-555-5555", "2015550123"])
        self.assertEqual(got[0], "(555) 555-5555")
        self.assertEqual(stats["not_valid"], 1)

    def test_extension_kept_and_bad_country_rejected(self):
        got, _ = run("standardize_phone", ["201-555-0123 ext. 42"])
        self.assertIn("42", got[0])
        with self.assertRaises(ops.PlanError):
            run("standardize_phone", ["2015550123"], country="XX")


class Email(unittest.TestCase):
    def test_valid_lowercased_invalid_kept(self):
        got, stats = run("standardize_email", ["John.Doe@Example.COM", "bad@", "a@b", "x@@y.com", "ok@sub.example.org"])
        self.assertEqual(got, ["john.doe@example.com", "bad@", "a@b", "x@@y.com", "ok@sub.example.org"])
        self.assertEqual(stats["unparsed"], 3)


class Dates(unittest.TestCase):
    def test_written_out_dates(self):
        got, stats = run("standardize_dates", ["March 3rd, 2024", "3 mars 2024", "2024-03-03", "03/03/2024"])
        self.assertEqual(got, ["2024-03-03"] * 4)
        self.assertEqual(stats["unparsed"], 0)

    def test_relative_dates_are_never_guessed(self):
        got, stats = run("standardize_dates", ["yesterday", "in 2 weeks", "3 days ago"])
        self.assertEqual(got, ["yesterday", "in 2 weeks", "3 days ago"])
        self.assertEqual(stats["unparsed"], 3)

    def test_dayfirst_reaches_the_fallback(self):
        self.assertEqual(run("standardize_dates", ["4th of 5 2024"], dayfirst=True)[0], ["2024-05-04"])


class SimilarValues(unittest.TestCase):
    def test_variants_grouped_most_common_first(self):
        s = pd.Series(["New York"] * 5 + ["new york"] * 2 + ["New  York "] + ["Boston"] * 3 + ["Bostn"])
        groups = common.similar_values(s)
        self.assertEqual(list(groups[0]), ["New York", "new york", "New  York "])
        self.assertEqual(groups[1], {"Boston": 3, "Bostn": 1})

    def test_ids_and_distinct_values_not_grouped(self):
        self.assertEqual(common.similar_values(pd.Series(["100234", "100243", "Austria", "Australia"])), [])

    def test_profile_shows_groups_only_for_unmasked_columns(self):
        df = pd.DataFrame({"city": ["Paris", "paris", "Rome"], "email": ["a@x.com", "A@x.com", "b@y.com"]})
        p = {c["name"]: c for c in common.profile(df, masked={"email"})["columns_detail"]}
        self.assertIn("similar_values", p["city"])
        self.assertNotIn("similar_values", p["email"])


if __name__ == "__main__":
    unittest.main()
