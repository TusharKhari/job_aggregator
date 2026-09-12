import unittest
from pathlib import Path
import tempfile
import shutil

from xing.xing_scraper import extract_xing_job_id, is_badge_or_meta, load_seen_keys as load_xing_seen, record_seen_keys as record_xing_seen
from indeed.indeed_scraper import load_seen_keys as load_indeed_seen, record_seen_keys as record_indeed_seen


class TestScrapers(unittest.TestCase):
    def test_xing_url_filtering(self):
        # Valid job URLs
        self.assertEqual(
            extract_xing_job_id("https://www.xing.com/jobs/essen-data-scientist-computer-vision-ki-157321265"),
            "157321265"
        )
        self.assertEqual(
            extract_xing_job_id("https://www.xing.com/jobs/hamburg-data-scientist-157803545?search_id=123"),
            "157803545"
        )
        # Invalid non-job URLs that were previously captured erroneously
        self.assertIsNone(extract_xing_job_id("https://www.xing.com/recruiting/jobs/create"))
        self.assertIsNone(extract_xing_job_id("https://www.xing.com/jobs/directory/a"))
        self.assertIsNone(extract_xing_job_id("https://www.xing.com/jobs/search?keywords=test"))
        self.assertIsNone(extract_xing_job_id("https://www.xing.com/jobs"))

    def test_badge_and_meta_filtering(self):
        # UI badges that previously polluted company names
        self.assertTrue(is_badge_or_meta("Urgently hiring"))
        self.assertTrue(is_badge_or_meta("Be an early applicant"))
        self.assertTrue(is_badge_or_meta("Top-Arbeitgeber"))
        self.assertTrue(is_badge_or_meta("Vor 2 Tagen"))
        self.assertTrue(is_badge_or_meta("Schnellbewerbung"))
        self.assertTrue(is_badge_or_meta("Remote"))
        self.assertTrue(is_badge_or_meta("Main Sections"))
        self.assertTrue(is_badge_or_meta("Jobs Directory"))

        # Real companies
        self.assertFalse(is_badge_or_meta("Generali Deutschland AG"))
        self.assertFalse(is_badge_or_meta("Process& GmbH"))
        self.assertFalse(is_badge_or_meta("HUK-COBURG VVaG"))
        self.assertFalse(is_badge_or_meta("Pflegia"))

    def test_seen_keys_persistence(self):
        temp_dir = tempfile.mkdtemp()
        try:
            temp_file = Path(temp_dir) / "seen_test.txt"
            self.assertEqual(load_xing_seen(temp_file), set())

            record_xing_seen({"job_123", "job_456"}, temp_file)
            loaded = load_xing_seen(temp_file)
            self.assertEqual(loaded, {"job_123", "job_456"})

            # Append new
            record_xing_seen({"job_789"}, temp_file)
            self.assertEqual(load_xing_seen(temp_file), {"job_123", "job_456", "job_789"})
        finally:
            shutil.rmtree(temp_dir)

    def test_24h_date_filter(self):
        from utils import is_within_24h

        # Positive cases (< 24 hours)
        self.assertTrue(is_within_24h("Vor 2 Stunden"))
        self.assertTrue(is_within_24h("Vor 45 Minuten"))
        self.assertTrue(is_within_24h("Heute"))
        self.assertTrue(is_within_24h("Today"))
        self.assertTrue(is_within_24h("Just posted"))
        self.assertTrue(is_within_24h("Vor 1 Tag"))
        self.assertTrue(is_within_24h("1 Tag"))
        self.assertTrue(is_within_24h("1 day ago"))
        self.assertTrue(is_within_24h("Aktiv vor wenigen Stunden"))
        self.assertTrue(is_within_24h("N/A", source="indeed"))

        # Negative cases (> 24 hours)
        self.assertFalse(is_within_24h("Vor 2 Tagen"))
        self.assertFalse(is_within_24h("Vor 3 Tagen"))
        self.assertFalse(is_within_24h("Vor 14 Tagen"))
        self.assertFalse(is_within_24h("Vor 1 Woche"))
        self.assertFalse(is_within_24h("Vor 2 Wochen"))
        self.assertFalse(is_within_24h("Vor 30+ Tagen"))
        self.assertFalse(is_within_24h("Vor einem Monat"))
        self.assertFalse(is_within_24h("3 days ago"))
        self.assertFalse(is_within_24h("N/A", source="xing"))

    def test_stepstone_extraction(self):
        from stepstone.stepstone import extract_stepstone_job_id, load_seen_keys, record_seen_keys
        temp_dir = tempfile.mkdtemp()
        try:
            # Valid Stepstone URLs
            self.assertEqual(
                extract_stepstone_job_id("https://www.stepstone.de/stellenangebote--Machine-Learning-Engineer-Muenchen--1234567-inline.html"),
                "1234567"
            )
            self.assertEqual(
                extract_stepstone_job_id("https://www.stepstone.de/stellenangebote--Data-Scientist-Stuttgart--9876543.html"),
                "9876543"
            )
            self.assertIsNone(extract_stepstone_job_id("https://www.stepstone.de/jobs/Data-Scientist/in-Deutschland"))

            # Seen keys test
            test_file = Path(temp_dir) / "stepstone_seen.txt"
            record_seen_keys({"1234567", "9876543"}, test_file)
            self.assertEqual(load_seen_keys(test_file), {"1234567", "9876543"})
        finally:
            shutil.rmtree(temp_dir)


if __name__ == "__main__":
    unittest.main()
