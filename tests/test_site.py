from pathlib import Path
import re
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
        email_pattern = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
        for name, path in PAGES.items():
            with self.subTest(name=name):
                html = read(path)
                self.assertIn(SUPPORT_EMAIL, html)
                found = email_pattern.findall(html)
                self.assertTrue(found, "no email address found in page")
                for address in found:
                    self.assertEqual(address, SUPPORT_EMAIL)

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

    def test_device_migration_faq(self):
        ja = read(PAGES["ja_support"])
        for value in [
            "クイックスタート",
            "iCloudバックアップ",
            "現在iPhone版だけ",
            "将来提供する予定",
            "時期は未定",
        ]:
            self.assertIn(value, ja)
        en = read(PAGES["en_support"])
        for value in [
            "Quick Start",
            "iCloud backup",
            "iPhone only",
            "planned for the future",
            "no release date",
        ]:
            self.assertIn(value, en)

    def test_privacy_disclosures(self):
        ja = read(PAGES["ja_privacy"])
        en = read(PAGES["en_privacy"])
        for value in ["OpenStreetMap", "Frankfurter", "MapKit", "Google Maps", "12か月"]:
            self.assertIn(value, ja)
        for value in ["OpenStreetMap", "Frankfurter", "MapKit", "Google Maps", "12 months"]:
            self.assertIn(value, en)

    def test_primary_color_meets_wcag_aa_contrast(self):
        """--primary は白背景・ミント背景の双方で通常文字4.5:1以上を満たす色であること"""
        css = (ROOT / "styles.css").read_text(encoding="utf-8")
        self.assertIn("--primary: #0f766e;", css)
        self.assertNotIn("--primary: #168f84;", css)

    def test_frankfurter_connection_info_disclosure(self):
        """Frankfurter API段落にIPアドレスなどの一般的な接続情報処理の開示が含まれること"""
        ja = read(PAGES["ja_privacy"])
        en = read(PAGES["en_privacy"])
        # 日本語: IPアドレスと一般的な接続情報の記述が必須
        self.assertIn("IPアドレス", ja)
        self.assertIn("一般的な接続情報", ja)
        # 英語: IP address と general connection information の記述が必須
        self.assertIn("IP address", en)
        self.assertIn("general connection information", en)


if __name__ == "__main__":
    unittest.main()
