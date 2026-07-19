import unittest
from unittest import mock

import tool_installer as ti


class CatalogIntegrityTests(unittest.TestCase):
    def test_every_entry_has_required_fields(self):
        for name, spec in ti.INSTALLABLE_TOOLS.items():
            for field in ("package", "import", "keywords", "desc"):
                self.assertIn(field, spec, f"{name} missing {field}")
            self.assertTrue(spec["keywords"], f"{name} has no keywords")
            self.assertIsInstance(spec["keywords"], list)


class FindMissingToolTests(unittest.TestCase):
    def test_matches_keyword_when_not_installed(self):
        with mock.patch.object(ti, "is_installed", return_value=False):
            self.assertEqual(
                ti.find_missing_tool_for_request("can you make a qr code for this"),
                "qrcode",
            )

    def test_no_offer_when_already_installed(self):
        with mock.patch.object(ti, "is_installed", return_value=True):
            self.assertIsNone(
                ti.find_missing_tool_for_request("make a qr code")
            )

    def test_unrelated_request_returns_none(self):
        with mock.patch.object(ti, "is_installed", return_value=False):
            self.assertIsNone(
                ti.find_missing_tool_for_request("what's the weather today")
            )

    def test_empty_text_returns_none(self):
        self.assertIsNone(ti.find_missing_tool_for_request(""))
        self.assertIsNone(ti.find_missing_tool_for_request(None))

    def test_translate_keyword(self):
        with mock.patch.object(ti, "is_installed", return_value=False):
            self.assertEqual(
                ti.find_missing_tool_for_request("translate hello into french"),
                "translate",
            )


class DueDiligenceTests(unittest.TestCase):
    def test_rejects_unknown_tool(self):
        result = ti.due_diligence("not-a-real-tool")
        self.assertFalse(result["ok"])

    def test_accepts_matching_package(self):
        payload = {"info": {"name": "qrcode", "version": "7.4.2"},
                   "releases": {"7.4.2": [{"yanked": False}]}}
        with mock.patch.object(ti, "_fetch_pypi", return_value=payload):
            result = ti.due_diligence("qrcode")
        self.assertTrue(result["ok"])
        self.assertEqual(result["version"], "7.4.2")

    def test_rejects_name_mismatch_typosquat(self):
        payload = {"info": {"name": "evil-lookalike", "version": "1.0"},
                   "releases": {"1.0": [{"yanked": False}]}}
        with mock.patch.object(ti, "_fetch_pypi", return_value=payload):
            result = ti.due_diligence("qrcode")
        self.assertFalse(result["ok"])
        self.assertIn("mismatch", result["reason"].lower())

    def test_rejects_fully_yanked_release(self):
        payload = {"info": {"name": "qrcode", "version": "7.4.2"},
                   "releases": {"7.4.2": [{"yanked": True}]}}
        with mock.patch.object(ti, "_fetch_pypi", return_value=payload):
            result = ti.due_diligence("qrcode")
        self.assertFalse(result["ok"])
        self.assertIn("yank", result["reason"].lower())

    def test_handles_pypi_unreachable(self):
        import urllib.error
        with mock.patch.object(ti, "_fetch_pypi",
                               side_effect=urllib.error.URLError("boom")):
            result = ti.due_diligence("qrcode")
        self.assertFalse(result["ok"])

    def test_treats_underscore_and_dash_as_equal(self):
        payload = {"info": {"name": "deep_translator", "version": "1.11.4"},
                   "releases": {"1.11.4": [{"yanked": False}]}}
        with mock.patch.object(ti, "_fetch_pypi", return_value=payload):
            result = ti.due_diligence("translate")  # package is deep-translator
        self.assertTrue(result["ok"])


class InstallTests(unittest.TestCase):
    def test_unknown_tool_not_installed(self):
        result = ti.install("not-a-real-tool")
        self.assertFalse(result["ok"])

    def test_success_path(self):
        completed = mock.Mock(returncode=0, stdout="ok", stderr="")
        with mock.patch.object(ti.subprocess, "run", return_value=completed):
            result = ti.install("qrcode")
        self.assertTrue(result["ok"])

    def test_failure_reports_last_line(self):
        completed = mock.Mock(returncode=1, stdout="", stderr="ERROR: no such package")
        with mock.patch.object(ti.subprocess, "run", return_value=completed):
            result = ti.install("qrcode")
        self.assertFalse(result["ok"])
        self.assertIn("no such package", result["message"])


if __name__ == "__main__":
    unittest.main()
