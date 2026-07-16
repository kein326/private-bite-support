from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
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


def extract_css_var(css: str, var_name: str) -> str:
    """CSS テキストから `--var-name: #rrggbb;` 形式の16進色値を抽出する。
    見つからない場合は AssertionError を送出する（テスト内での利用を想定）。"""
    match = re.search(rf"{re.escape(var_name)}\s*:\s*(#[0-9a-fA-F]{{6}})\s*;", css)
    if not match:
        raise AssertionError(f"{var_name} が css 内に見つかりません")
    return match.group(1)


def relative_luminance(hex_color: str) -> float:
    """WCAG の sRGB 相対輝度式で16進色（#rrggbb）から相対輝度 L を計算する。"""
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (0, 2, 4))

    def channel_linear(c_255: int) -> float:
        c_prime = c_255 / 255
        if c_prime <= 0.03928:
            return c_prime / 12.92
        return ((c_prime + 0.055) / 1.055) ** 2.4

    r_lin, g_lin, b_lin = channel_linear(r), channel_linear(g), channel_linear(b)
    return 0.2126 * r_lin + 0.7152 * g_lin + 0.0722 * b_lin


def contrast_ratio(hex_color_a: str, hex_color_b: str) -> float:
    """WCAG のコントラスト比式で2つの16進色間のコントラスト比を計算する。"""
    l_a = relative_luminance(hex_color_a)
    l_b = relative_luminance(hex_color_b)
    lighter, darker = max(l_a, l_b), min(l_a, l_b)
    return (lighter + 0.05) / (darker + 0.05)


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


class _DlPairParser(HTMLParser):
    """<dl>内の<dt>テキストと直後の<dd>テキストをペアとして抽出するヘルパー。
    ページ全体の単語一致ではなく、質問(dt)と回答(dd)の対応関係を保ったまま
    回答本文だけを検査できるようにするため、標準ライブラリの HTMLParser だけで
    構造検査を行う（_LinkExtractingParser と同じ方式）。"""

    def __init__(self):
        super().__init__()
        self.pairs = []
        self._current_tag = None
        self._current_text = []
        self._pending_dt = None

    def handle_starttag(self, tag, attrs):
        if tag in ("dt", "dd"):
            self._current_tag = tag
            self._current_text = []

    def handle_endtag(self, tag):
        if tag == "dt" and self._current_tag == "dt":
            self._pending_dt = "".join(self._current_text).strip()
            self._current_tag = None
        elif tag == "dd" and self._current_tag == "dd":
            dd_text = "".join(self._current_text).strip()
            if self._pending_dt is not None:
                self.pairs.append((self._pending_dt, dd_text))
                self._pending_dt = None
            self._current_tag = None

    def handle_data(self, data):
        if self._current_tag in ("dt", "dd"):
            self._current_text.append(data)


def extract_dt_dd_pairs(html: str) -> list:
    """html.parser.HTMLParser のみを使い、<dl>内の<dt>テキストと直後の<dd>テキストを
    (question, answer) のタプルの一覧として抽出する。"""
    parser = _DlPairParser()
    parser.feed(html)
    return parser.pairs


def _answer_for_question(pairs, question_substring):
    """dt/dd ペアの一覧から、dt に question_substring を含む dd テキストを1件返す。
    ページ全体ではなく対応する回答だけを検査対象にするための橋渡し。"""
    matches = [dd for dt, dd in pairs if question_substring in dt]
    if not matches:
        raise AssertionError(
            f"no <dt> containing {question_substring!r} found in extracted pairs"
        )
    assert len(matches) == 1, (
        f"expected exactly one <dt> containing {question_substring!r}, found {len(matches)}"
    )
    return matches[0]


def _assert_contains_all(text, phrases, label):
    for phrase in phrases:
        assert phrase in text, f"[{label}] missing required phrase {phrase!r} in: {text!r}"


def _assert_contains_none(text, phrases, label):
    for phrase in phrases:
        assert phrase not in text, f"[{label}] forbidden phrase {phrase!r} found in: {text!r}"


def is_valid_support_mailto(href: str) -> bool:
    """mailto: URI (RFC 6068) を urllib.parse で分解し、次をすべて満たす場合だけ
    True を返す。startswith の前方一致検査では、
    mailto:privatebite.support@icloud.com,other@example.com?subject=x のような
    複数宛先を誤って許可してしまうため、厳密な分解検査に置き換える。

    - scheme が "mailto" である。
    - 宛先（urlsplit の path 部分をカンマ区切りで分割したもの）が1件だけであり、
      その1件が SUPPORT_EMAIL と完全一致する。
    - クエリ文字列のキーが "subject" だけであり、"cc"・"bcc"・その他未知のキーを
      含まない。
    """
    parts = urlsplit(href)
    if parts.scheme != "mailto":
        return False

    recipients = parts.path.split(",")
    if len(recipients) != 1:
        return False
    if recipients[0] != SUPPORT_EMAIL:
        return False

    query_keys = set(parse_qs(parts.query, keep_blank_values=True).keys())
    if query_keys != {"subject"}:
        return False

    return True


_URL_FUNCTION_PATTERN = re.compile(
    r"""url\(\s*(['"]?)(?P<value>[^'")]*)\1\s*\)""",
    re.IGNORECASE,
)
_IMPORT_OR_FONT_FACE_PATTERN = re.compile(r"@import|@font-face", re.IGNORECASE)
_EXTERNAL_URL_PREFIXES = ("http:", "https:", "//")


def extract_external_css_urls(css: str) -> list:
    """CSS全文から url(...) の中身（引用符・前後の空白を除いた値）をすべて抽出し、
    http: / https: / // のいずれかで始まる外部参照だけを一覧として返す。
    url( の大文字小文字、引用符の有無（シングル/ダブル/なし）、括弧内側の空白の
    バリエーションを問わずマッチするよう re.IGNORECASE を使った正規表現で解析する
    （部分一致の "url(http" では検出できない引用符付き・大文字表記に対応するため）。"""
    external = []
    for match in _URL_FUNCTION_PATTERN.finditer(css):
        value = match.group("value").strip()
        if value.lower().startswith(_EXTERNAL_URL_PREFIXES):
            external.append(value)
    return external


def css_has_import_or_font_face(css: str) -> bool:
    """CSS全文に @import または @font-face が含まれるか、大文字小文字を問わず判定する。"""
    return _IMPORT_OR_FONT_FACE_PATTERN.search(css) is not None


def assert_android_faq_answer_complete(dd_text: str, lang: str) -> None:
    """Android機種変更FAQの回答(dd)本文だけを対象に、現在Androidにインストール・
    利用・バックアップ復元ができないこと、Android版は将来提供予定であること、
    提供時期は未定であること、現在のバックアップと将来のAndroid版との互換性が
    未定であること、将来の復元を保証しないことをすべて満たすか検査する。
    いずれか欠けている、または矛盾した内容（例: 復元を保証すると書かれている）
    の場合は AssertionError を送出する。"""
    if lang == "ja":
        required = [
            "現在iPhone版だけ",
            "将来提供する予定",
            "時期は未定",
            "インストール",
            "利用",
            "バックアップ",
            "復元",
            "互換性",
            "未定",
            "保証されません",
        ]
    elif lang == "en":
        required = [
            "iPhone",
            "planned",
            "no release date",
            "cannot be installed",
            "used on Android",
            "backups cannot be restored",
            "Compatibility",
            "undecided",
            "not guaranteed",
        ]
    else:
        raise ValueError(f"unsupported lang: {lang!r}")
    _assert_contains_all(dd_text, required, f"android-faq-{lang}")


def assert_iphone_migration_faq_answer_complete(dd_text: str, lang: str) -> None:
    """iPhone間機種変更FAQの回答(dd)本文だけを対象に、クイックスタートまたは
    iCloudバックアップで通常は記録・写真が引き継がれること、データエクスポートは
    必須ではない任意の追加バックアップであることを検査する。"""
    if lang == "ja":
        required = ["クイックスタート", "iCloudバックアップ", "通常は", "引き継が", "追加のバックアップ"]
        forbidden = ["必須", "しなければ"]
    elif lang == "en":
        required = ["Quick Start", "iCloud backup", "usually", "carried over", "additional backup"]
        forbidden = ["must", "is required"]
    else:
        raise ValueError(f"unsupported lang: {lang!r}")
    _assert_contains_all(dd_text, required, f"iphone-migration-faq-{lang}")
    _assert_contains_none(dd_text, forbidden, f"iphone-migration-faq-{lang}")


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
            "only for iPhone",
            "is planned",
            "no release date",
        ]:
            self.assertIn(value, en)

    def test_android_faq_answer_is_complete_for_its_own_dd(self):
        """『Androidへ機種変更しても使えますか？』の dt に対応する dd 本文だけを
        検査する（ページ全体の単語一致では、別の質問の dd に語句が含まれているだけ
        でも成功してしまうため、dt/dd 対応付けを経由する）。"""
        ja_pairs = extract_dt_dd_pairs(read(PAGES["ja_support"]))
        ja_answer = _answer_for_question(ja_pairs, "Androidへ機種変更")
        assert_android_faq_answer_complete(ja_answer, "ja")

        en_pairs = extract_dt_dd_pairs(read(PAGES["en_support"]))
        en_answer = _answer_for_question(en_pairs, "switching to Android")
        assert_android_faq_answer_complete(en_answer, "en")

    def test_iphone_migration_faq_answer_is_complete_for_its_own_dd(self):
        """『機種変更しても記録を引き継げますか？』の dt に対応する dd 本文だけを
        検査する。クイックスタート/iCloudバックアップでの引き継ぎと、データ
        エクスポートが必須ではない任意の追加バックアップであることを確認する。"""
        ja_pairs = extract_dt_dd_pairs(read(PAGES["ja_support"]))
        ja_answer = _answer_for_question(ja_pairs, "機種変更しても記録を引き継げます")
        assert_iphone_migration_faq_answer_complete(ja_answer, "ja")

        en_pairs = extract_dt_dd_pairs(read(PAGES["en_support"]))
        en_answer = _answer_for_question(en_pairs, "transfer to a new iPhone")
        assert_iphone_migration_faq_answer_complete(en_answer, "en")

    def test_faq_pair_validation_rejects_contradictory_synthetic_answer(self):
        """dt/dd 対応付けロジックと検査関数が、矛盾した回答（将来のAndroid版での
        復元を保証すると書かれている、データエクスポートを必須と書いている等）に
        対して意図通り AssertionError を送出することを、合成HTMLで確認する。
        実ファイルではなく文字列として組み立てたHTMLスニペットを使う。"""
        contradictory_android_ja_html = """
        <dl>
          <dt>Androidへ機種変更しても使えますか？</dt>
          <dd>Private Biteは現在iPhone版だけを提供しています。Android版は将来提供する予定ですが、
          提供時期は未定です。現時点ではAndroidへインストールして利用したりバックアップを復元したり
          することはできませんが、将来のAndroid版でも今のバックアップは必ず復元できることを保証します。</dd>
        </dl>
        """
        pairs = extract_dt_dd_pairs(contradictory_android_ja_html)
        answer = _answer_for_question(pairs, "Androidへ機種変更")
        with self.assertRaises(AssertionError):
            assert_android_faq_answer_complete(answer, "ja")

        contradictory_android_en_html = """
        <dl>
          <dt>Can I use Private Bite after switching to Android?</dt>
          <dd>Private Bite is currently available for iPhone only. An Android version is planned
          for the future, but no release date has been set. At this time, Private Bite cannot be
          installed or used on Android, but future backups are always guaranteed to restore fine.</dd>
        </dl>
        """
        pairs_en = extract_dt_dd_pairs(contradictory_android_en_html)
        answer_en = _answer_for_question(pairs_en, "switching to Android")
        with self.assertRaises(AssertionError):
            assert_android_faq_answer_complete(answer_en, "en")

        contradictory_migration_ja_html = """
        <dl>
          <dt>機種変更しても記録を引き継げますか？</dt>
          <dd>クイックスタートまたはiCloudバックアップから新しいiPhoneへ移行した場合、
          Private Biteの記録と写真も通常は引き継がれます。移行前に設定画面の
          「データをエクスポート」で追加のバックアップを必ず保存しなければなりません。</dd>
        </dl>
        """
        pairs_mig = extract_dt_dd_pairs(contradictory_migration_ja_html)
        answer_mig = _answer_for_question(pairs_mig, "機種変更しても記録を引き継げます")
        with self.assertRaises(AssertionError):
            assert_iphone_migration_faq_answer_complete(answer_mig, "ja")

    def test_privacy_disclosures(self):
        ja = read(PAGES["ja_privacy"])
        en = read(PAGES["en_privacy"])
        for value in ["OpenStreetMap", "Frankfurter", "MapKit", "Google Maps", "12か月"]:
            self.assertIn(value, ja)
        for value in ["OpenStreetMap", "Frankfurter", "MapKit", "Google Maps", "12 months"]:
            self.assertIn(value, en)

    def test_primary_color_meets_wcag_aa_contrast(self):
        """--primary は --surface（白背景）・--background（ミント背景）の双方で
        通常文字4.5:1以上のWCAGコントラスト比を実値計算で満たすこと"""
        css = (ROOT / "styles.css").read_text(encoding="utf-8")
        primary = extract_css_var(css, "--primary")
        surface = extract_css_var(css, "--surface")
        background = extract_css_var(css, "--background")

        ratio_vs_surface = contrast_ratio(primary, surface)
        ratio_vs_background = contrast_ratio(primary, background)

        self.assertGreaterEqual(ratio_vs_surface, 4.5)
        self.assertGreaterEqual(ratio_vs_background, 4.5)

    def test_contrast_ratio_below_threshold_is_detected(self):
        """薄い色同士の組み合わせは4.5:1を下回ると判定されること（否定テスト）。
        --primary: #cccccc; と --surface: #ffffff; を想定した合成CSSで検証する。"""
        low_contrast_css = "--primary: #cccccc;\n--surface: #ffffff;\n"
        primary = extract_css_var(low_contrast_css, "--primary")
        surface = extract_css_var(low_contrast_css, "--surface")

        ratio = contrast_ratio(primary, surface)

        self.assertLess(ratio, 4.5)

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
        """全ページの mailto リンクが、宛先1件が privatebite.support@icloud.com と
        完全一致し、cc/bcc を持たず、subject 以外のクエリキーを持たないこと。
        前方一致（startswith）ではなく urllib.parse による厳密検査を行うため、
        mailto:privatebite.support@icloud.com,other@example.com?subject=x のような
        複数宛先を誤って許可しない。"""
        for name, path in PAGES.items():
            links = extract_links(read(path))
            mailto_links = [link for link in links if link["href"].startswith("mailto:")]
            with self.subTest(name=name):
                self.assertTrue(mailto_links, "no mailto link found")
                for link in mailto_links:
                    self.assertTrue(
                        is_valid_support_mailto(link["href"]),
                        link["href"],
                    )

    def test_mailto_validation_rejects_multiple_recipients(self):
        """宛先がカンマ区切りで複数指定された mailto は拒否されること"""
        self.assertFalse(
            is_valid_support_mailto(
                f"mailto:{SUPPORT_EMAIL},other@example.com?subject=x"
            )
        )

    def test_mailto_validation_rejects_cc(self):
        """cc パラメータを持つ mailto は拒否されること"""
        self.assertFalse(
            is_valid_support_mailto(f"mailto:{SUPPORT_EMAIL}?cc=other@example.com")
        )

    def test_mailto_validation_rejects_bcc(self):
        """bcc パラメータを持つ mailto は拒否されること"""
        self.assertFalse(
            is_valid_support_mailto(f"mailto:{SUPPORT_EMAIL}?bcc=other@example.com")
        )

    def test_stylesheet_has_no_forbidden_patterns(self):
        """styles.css に @import・外部URL参照・外部フォント・外部CDN・スクリプトが
        含まれないこと（本文HTMLだけでなくCSS自体を対象にした検査）。
        url(...) は引用符・空白・大文字小文字を正規化した厳密な正規表現解析で、
        @import・@font-face は大文字小文字を問わない検査で判定する。"""
        css_raw = (ROOT / "styles.css").read_text(encoding="utf-8")
        self.assertEqual(extract_external_css_urls(css_raw), [])
        self.assertFalse(css_has_import_or_font_face(css_raw))

        css = css_raw.lower()
        forbidden = [
            "googleapis",
            "jsdelivr",
            "<script",
        ]
        for value in forbidden:
            with self.subTest(value=value):
                self.assertNotIn(value, css)

    def test_extract_external_css_urls_detects_quoted_and_spaced_forms(self):
        """url(...) の引用符付き・大文字・前後空白のバリエーションを、部分一致
        (url(http) では検出できないケースも含めて正規表現解析で検出できること"""
        with self.subTest(value="quoted https url"):
            css = 'background-image: url("https://cdn.example.com/image.png");'
            self.assertEqual(
                extract_external_css_urls(css),
                ["https://cdn.example.com/image.png"],
            )

        with self.subTest(value="uppercase URL with spaces and single quotes"):
            css = "background-image: URL( '//cdn.example.com/image.png' );"
            self.assertEqual(
                extract_external_css_urls(css),
                ["//cdn.example.com/image.png"],
            )

        with self.subTest(value="@import"):
            css = '@import "https://cdn.example.com/site.css";'
            self.assertTrue(css_has_import_or_font_face(css))

        with self.subTest(value="@font-face with url"):
            css = (
                "@font-face { font-family: Example; "
                "src: url(https://cdn.example.com/font.woff2); }"
            )
            self.assertTrue(css_has_import_or_font_face(css))
            self.assertEqual(
                extract_external_css_urls(css),
                ["https://cdn.example.com/font.woff2"],
            )


if __name__ == "__main__":
    unittest.main()
