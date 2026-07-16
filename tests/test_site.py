from html.parser import HTMLParser
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


class _LinkExtractingParser(HTMLParser):
    """<a href="..."> 要素をパースし、href・可視テキスト・直近の nav/footer/section/header
    祖先要素（class 付きならタグ名.class）を記録するヘルパー。BeautifulSoup 等の外部
    ライブラリは使わず、標準ライブラリの HTMLParser だけで構造検査を可能にする。"""

    CONTAINER_TAGS = ("nav", "footer", "section", "header")

    def __init__(self):
        super().__init__()
        self.links = []
        self._stack = []
        self._current_link = None

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        self._stack.append((tag, attrs_dict))
        if tag == "a":
            self._current_link = {
                "href": attrs_dict.get("href", ""),
                "text": "",
                "lang": attrs_dict.get("lang", ""),
                "container": self._nearest_container(),
            }

    def handle_endtag(self, tag):
        if tag == "a" and self._current_link is not None:
            self.links.append(self._current_link)
            self._current_link = None
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i][0] == tag:
                del self._stack[i:]
                break

    def handle_data(self, data):
        if self._current_link is not None:
            self._current_link["text"] += data

    def _nearest_container(self):
        for tag, attrs in reversed(self._stack):
            if tag in self.CONTAINER_TAGS:
                cls = attrs.get("class", "")
                return f"{tag}.{cls}" if cls else tag
        return None


def extract_links(html: str) -> list:
    """html.parser.HTMLParser のみを使って <a href="..."> 要素を抽出する。
    各要素は {"href", "text", "lang", "container"} の dict。"""
    parser = _LinkExtractingParser()
    parser.feed(html)
    return parser.links


def _link_in_container(links, container):
    matches = [link["href"] for link in links if link["container"] == container]
    assert len(matches) == 1, (
        f"expected exactly one link in <{container}>, found {matches!r}"
    )
    return matches[0]


def _language_link_href(links):
    return _link_in_container(links, "nav.language")


def _footer_link_href(links):
    return _link_in_container(links, "footer")


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

    def test_language_links_are_mutual(self):
        """各ページの言語切替リンク（nav.language 内の href）が対応するページを正しく指すこと"""
        cases = {
            "ja_support": "en/",
            "en_support": "../",
            "ja_privacy": "../en/privacy/",
            "en_privacy": "../../privacy/",
        }
        for name, expected_href in cases.items():
            with self.subTest(name=name):
                links = extract_links(read(PAGES[name]))
                self.assertEqual(_language_link_href(links), expected_href)

    def test_support_links_to_privacy(self):
        """サポートページのフッターリンクが同言語のプライバシーポリシーを指すこと"""
        for name in ("ja_support", "en_support"):
            with self.subTest(name=name):
                links = extract_links(read(PAGES[name]))
                self.assertEqual(_footer_link_href(links), "privacy/")

    def test_privacy_links_back_to_same_language_support(self):
        """プライバシーポリシーのフッターリンクが同言語のサポートへ戻ること"""
        for name in ("ja_privacy", "en_privacy"):
            with self.subTest(name=name):
                links = extract_links(read(PAGES[name]))
                self.assertEqual(_footer_link_href(links), "../")

    def test_mailto_links_target_support_address(self):
        """全ページの mailto リンクが privatebite.support@icloud.com を指すこと
        （本文全体の正規表現検査ではなく、<a href="mailto:..."> の href 値そのものを検査する）"""
        for name, path in PAGES.items():
            links = extract_links(read(path))
            mailto_links = [link for link in links if link["href"].startswith("mailto:")]
            with self.subTest(name=name):
                self.assertTrue(mailto_links, "no mailto link found")
                for link in mailto_links:
                    self.assertTrue(
                        link["href"].startswith(f"mailto:{SUPPORT_EMAIL}"),
                        link["href"],
                    )

    def test_stylesheet_has_no_forbidden_patterns(self):
        """styles.css に @import・外部URL参照・外部フォント・外部CDN・スクリプトが
        含まれないこと（本文HTMLだけでなくCSS自体を対象にした検査）"""
        css = (ROOT / "styles.css").read_text(encoding="utf-8").lower()
        forbidden = [
            "@import",
            "url(http",
            "googleapis",
            "jsdelivr",
            "<script",
        ]
        for value in forbidden:
            with self.subTest(value=value):
                self.assertNotIn(value, css)


if __name__ == "__main__":
    unittest.main()
