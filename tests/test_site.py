from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SUPPORT_EMAIL = "privatebite.support@icloud.com"
PAGES = {
    "ja_support": ROOT / "index.html",
    "ja_privacy": ROOT / "privacy" / "index.html",
    "en_support": ROOT / "en" / "index.html",
    "en_privacy": ROOT / "en" / "privacy" / "index.html",
}


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class SiteContractTest(unittest.TestCase):
    def test_all_required_pages_exist(self):
        for name, path in PAGES.items():
            with self.subTest(name=name):
                self.assertTrue(path.is_file(), path)

    def test_shared_stylesheet_exists(self):
        self.assertTrue((ROOT / "styles.css").is_file())

    def test_support_email_is_consistent(self):
        for name, path in PAGES.items():
            with self.subTest(name=name):
                html = read(path)
                self.assertIn(SUPPORT_EMAIL, html)
                self.assertNotIn("kei@synet.co.jp", html)

    def test_languages_and_titles(self):
        self.assertIn('lang="ja"', read(PAGES["ja_support"]))
        self.assertIn('lang="ja"', read(PAGES["ja_privacy"]))
        self.assertIn('lang="en"', read(PAGES["en_support"]))
        self.assertIn('lang="en"', read(PAGES["en_privacy"]))
        self.assertIn("Private Bite サポート", read(PAGES["ja_support"]))
        self.assertIn("プライバシーポリシー", read(PAGES["ja_privacy"]))
        self.assertIn("Private Bite Support", read(PAGES["en_support"]))
        self.assertIn("Privacy Policy", read(PAGES["en_privacy"]))

    def test_no_tracking_or_script(self):
        forbidden = [
            "<script",
            "googletagmanager",
            "google-analytics",
            "facebook.net",
            "doubleclick",
            "document.cookie",
            "fonts.googleapis.com",
            "cdn.jsdelivr.net",
        ]
        for name, path in PAGES.items():
            html = read(path).lower()
            with self.subTest(name=name):
                for value in forbidden:
                    self.assertNotIn(value, html)

    def test_privacy_disclosures(self):
        ja = read(PAGES["ja_privacy"])
        en = read(PAGES["en_privacy"])
        for value in ["OpenStreetMap", "Frankfurter", "MapKit", "Google Maps", "12か月"]:
            self.assertIn(value, ja)
        for value in ["OpenStreetMap", "Frankfurter", "MapKit", "Google Maps", "12 months"]:
            self.assertIn(value, en)


if __name__ == "__main__":
    unittest.main()
