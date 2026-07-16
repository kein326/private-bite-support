from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
import csv
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
SUPPORT_EMAIL = "privatebite.support@icloud.com"
PAGES = {
    "ja_support": ROOT / "index.html",
    "ja_privacy": ROOT / "privacy" / "index.html",
    "en_support": ROOT / "en" / "index.html",
    "en_privacy": ROOT / "en" / "privacy" / "index.html",
    "ja_import": ROOT / "import" / "index.html",
    "en_import": ROOT / "en" / "import" / "index.html",
}

CSV_TEMPLATES = {
    "ja": ROOT / "downloads" / "private-bite-import-v1-ja.csv",
    "en": ROOT / "downloads" / "private-bite-import-v1-en.csv",
}

# 正式仕様の17列（英語キー）。CsvImportSchema の列順を固定する。
EN_HEADERS = [
    "visit_group", "visit_date", "shop_name", "dish_name",
    "visit_type", "city", "country_code", "shop_address",
    "price", "currency_code", "taste_rating", "category",
    "cost_rating", "atmosphere_rating", "service_rating",
    "memo", "revisit_flag",
]

# 日本語版テンプレートのヘッダー表示名（EN_HEADERS と同じ列順）。
JA_HEADER_LABELS = [
    "訪問グループ", "訪問日", "店舗名", "料理名",
    "利用方法", "都市", "国コード", "店舗住所",
    "価格", "通貨コード", "味評価", "カテゴリ",
    "コスパ評価", "雰囲気評価", "サービス評価",
    "メモ", "再訪したい",
]

# 英語版テンプレートのヘッダー表示名（EN_HEADERS と同じ列順）。
EN_HEADER_LABELS = [
    "Visit Group", "Visit Date", "Shop Name", "Dish Name",
    "Visit Type", "City", "Country Code", "Shop Address",
    "Price", "Currency Code", "Taste Rating", "Category",
    "Cost Rating", "Atmosphere Rating", "Service Rating",
    "Memo", "Want to Revisit",
]

# 必須列（アプリ側 CsvImportSchema.requiredColumnKeys と同じ3列）のインデックス。
REQUIRED_COLUMN_INDICES = [
    EN_HEADERS.index("visit_date"),
    EN_HEADERS.index("shop_name"),
    EN_HEADERS.index("dish_name"),
]

# 現行アプリが対応する20通貨コード
# （private_bite の CsvImportSchema.supportedCurrencyCodes と同じ集合）。
SUPPORTED_CURRENCY_CODES = {
    "JPY", "USD", "EUR", "GBP", "CNY", "KRW", "THB", "TWD", "HKD", "SGD",
    "AUD", "CAD", "CHF", "MXN", "INR", "IDR", "PHP", "VND", "MYR", "BRL",
}

DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def read_csv_bytes(path: Path) -> bytes:
    return path.read_bytes()


def parse_csv_bom(path: Path) -> list:
    """UTF-8 BOM付きCSVファイルを標準csvモジュールで解析し、行のリストを返す。"""
    text = path.read_bytes().decode("utf-8-sig")
    return list(csv.reader(text.splitlines()))


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def extract_css_var(css: str, var_name: str) -> str:
    """CSS テキストから `--var-name: #rrggbb;` 形式の16進色値を抽出する。
    このサイトは --primary・--surface・--background を :root で一度だけ
    定義する契約のため、定義が0件（見つからない）または2件以上（レスポンシブ
    な上書きなどで重複定義されている）の場合は、どちらの値かを黙って選ばずに
    AssertionError を送出する（テスト内での利用を想定）。"""
    pattern = re.compile(rf"{re.escape(var_name)}\s*:\s*(#[0-9a-fA-F]{{6}})\s*;")
    matches = pattern.findall(css)
    if len(matches) != 1:
        raise AssertionError(
            f"{var_name} は css 内でちょうど1回定義されている必要がありますが、"
            f"{len(matches)}件見つかりました"
        )
    return matches[0]


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


def _extract_mailto_links(links):
    """抽出済みリンク一覧から mailto: リンクだけを、scheme の大文字小文字を
    問わずに抽出する。href.startswith("mailto:") のような前方一致だと、
    大文字の "MAILTO:" が検査対象から漏れてしまうため、
    urlsplit(href).scheme.lower() == "mailto" で判定する。"""
    return [link for link in links if urlsplit(link["href"]).scheme.lower() == "mailto"]


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


_TITLE_PATTERN = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def extract_title(html: str) -> str:
    """<title>...</title> の中身を1つ取り出す。見つからなければ AssertionError。"""
    match = _TITLE_PATTERN.search(html)
    if not match:
        raise AssertionError("no <title> found")
    return match.group(1).strip()


_H2_PATTERN = re.compile(r"<h2>(.*?)</h2>", re.DOTALL)


def extract_h2_texts(html: str) -> list:
    """ページ内の <h2>...</h2> テキストを出現順のリストで返す（見出し構成の検査用）。"""
    return [re.sub(r"\s+", " ", raw).strip() for raw in _H2_PATTERN.findall(html)]


class _TableRowParser(HTMLParser):
    """<table> 内の <tr> ごとに <td>/<th> のテキストをリストとして抽出するヘルパー。
    <code>や<strong>などのネストしたインラインタグを含むセルでも、そのセル内の
    テキストをまとめて1つの文字列として扱う（_DlPairParser と同じ標準ライブラリ方式）。"""

    def __init__(self):
        super().__init__()
        self.rows = []
        self._current_row = None
        self._cell_depth = 0
        self._cell_text = []

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._current_row = []
        elif tag in ("td", "th"):
            self._cell_depth += 1
            if self._cell_depth == 1:
                self._cell_text = []

    def handle_endtag(self, tag):
        if tag in ("td", "th"):
            if self._cell_depth > 0:
                self._cell_depth -= 1
                if self._cell_depth == 0 and self._current_row is not None:
                    self._current_row.append("".join(self._cell_text).strip())
        elif tag == "tr" and self._current_row is not None:
            self.rows.append(self._current_row)
            self._current_row = None

    def handle_data(self, data):
        if self._cell_depth > 0:
            self._cell_text.append(data)


def extract_table_rows(html: str) -> list:
    """html.parser.HTMLParser のみを使い、<table>内の各<tr>を
    セルテキストのリストとして抽出する（ヘッダー行含む出現順）。"""
    parser = _TableRowParser()
    parser.feed(html)
    return parser.rows


def _resolve_relative_link(page_path: Path, href: str) -> Path:
    """ページからの相対リンクを実ファイルパスへ解決する。ディレクトリを指す
    （末尾が "/"）場合は index.html を補う。"""
    if href.endswith("/"):
        return (page_path.parent / href / "index.html").resolve()
    return (page_path.parent / href).resolve()


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

    - scheme が "mailto" である（大文字小文字を問わない。"MAILTO:" のような
      表記も許可する）。
    - 宛先（urlsplit の path 部分をカンマ区切りで分割したもの）が1件だけであり、
      その1件が SUPPORT_EMAIL と完全一致する。
    - クエリ文字列のキーが "subject" だけであり、"cc"・"bcc"・その他未知のキーを
      含まない。
    """
    parts = urlsplit(href)
    if parts.scheme.lower() != "mailto":
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
    r"""url\(\s*(?:"(?P<dq>[^"]*)"|'(?P<sq>[^']*)'|(?P<uq>[^'")]*))\s*\)""",
    re.IGNORECASE,
)
_IMPORT_OR_FONT_FACE_PATTERN = re.compile(r"@import|@font-face", re.IGNORECASE)
_EXTERNAL_URL_PREFIXES = ("http:", "https:", "//")


def extract_external_css_urls(css: str) -> list:
    """CSS全文から url(...) の中身（引用符・前後の空白を除いた値）をすべて抽出し、
    http: / https: / // のいずれかで始まる外部参照だけを一覧として返す。
    url( の大文字小文字、引用符の有無（シングル/ダブル/なし）、括弧内側の空白の
    バリエーションを問わずマッチするよう re.IGNORECASE を使った正規表現で解析する
    （部分一致の "url(http" では検出できない引用符付き・大文字表記に対応するため）。
    ダブルクォート・シングルクォート・引用符なしを別々の選択肢として扱うことで、
    値の内部に反対側の引用符（例: url("...o'neil.png")）が含まれていても、
    開始引用符と同じ引用符が再び現れるまでを正しく値として取得する。"""
    external = []
    for match in _URL_FUNCTION_PATTERN.finditer(css):
        if match.group("dq") is not None:
            value = match.group("dq")
        elif match.group("sq") is not None:
            value = match.group("sq")
        else:
            value = match.group("uq")
        value = value.strip()
        if value.lower().startswith(_EXTERNAL_URL_PREFIXES):
            external.append(value)
    return external


def css_has_import_or_font_face(css: str) -> bool:
    """CSS全文に @import または @font-face が含まれるか、大文字小文字を問わず判定する。"""
    return _IMPORT_OR_FONT_FACE_PATTERN.search(css) is not None


_ANDROID_FAQ_FORBIDDEN_CONTRADICTIONS = {
    "ja": ["必ず復元", "復元を保証", "保証します", "保証されます"],
    "en": [
        "is guaranteed",
        "restoration is guaranteed",
        "will always restore",
        "guaranteed to restore",
    ],
}


def assert_android_faq_answer_complete(dd_text: str, lang: str) -> None:
    """Android機種変更FAQの回答(dd)本文だけを対象に、現在Androidにインストール・
    利用・バックアップ復元ができないこと、Android版は将来提供予定であること、
    提供時期は未定であること、現在のバックアップと将来のAndroid版との互換性が
    未定であること、将来の復元を保証しないことをすべて満たすか検査する。
    いずれか欠けている、または矛盾した内容（例: 復元を保証すると書かれている）
    の場合は AssertionError を送出する。必須語句の存在確認だけでは、正しい
    回答の末尾に反対の意味の文（「将来のAndroid版では必ず復元できることを
    保証します」等）が追記されても検出できないため、既知の矛盾表現も
    別途拒否する。"""
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
    _assert_contains_none(
        dd_text,
        _ANDROID_FAQ_FORBIDDEN_CONTRADICTIONS[lang],
        f"android-faq-contradiction-{lang}",
    )


def assert_iphone_migration_faq_answer_complete(dd_text: str, lang: str) -> None:
    """iPhone間機種変更FAQの回答(dd)本文だけを対象に、クイックスタートまたは
    iCloudバックアップで通常は記録・写真が引き継がれること、データエクスポートは
    必須ではない任意の追加バックアップであることを検査する。"""
    if lang == "ja":
        required = [
            "クイックスタート", "iCloudバックアップ", "通常は", "引き継が",
            "追加のバックアップ", "記録", "写真",
        ]
        forbidden = ["必須", "しなければ"]
    elif lang == "en":
        required = [
            "Quick Start", "iCloud backup", "usually", "carried over",
            "additional backup", "records", "photos",
        ]
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

    def test_android_faq_rejects_appended_contradiction(self):
        """必須語句の存在検査だけでは、正しい回答の末尾に反対の意味の文が
        追記されても検出できない（既存の合成テストは必須語句を削って矛盾を
        作っていたため、必須語句をすべて満たしたまま矛盾を追記するケースを
        見逃していた）。実ファイルの正しい回答の末尾へ矛盾する一文を追記し、
        AssertionError が送出されることを確認する。"""
        ja_pairs = extract_dt_dd_pairs(read(PAGES["ja_support"]))
        ja_answer = _answer_for_question(ja_pairs, "Androidへ機種変更")
        ja_contradicted = ja_answer + "ただし、将来のAndroid版では必ず復元できることを保証します。"
        with self.assertRaises(AssertionError):
            assert_android_faq_answer_complete(ja_contradicted, "ja")

        en_pairs = extract_dt_dd_pairs(read(PAGES["en_support"]))
        en_answer = _answer_for_question(en_pairs, "switching to Android")
        en_contradicted = (
            en_answer + " However, restoration on the future Android version is guaranteed."
        )
        with self.assertRaises(AssertionError):
            assert_android_faq_answer_complete(en_contradicted, "en")

    def test_iphone_migration_faq_requires_records_and_photos_mention(self):
        """機種変更FAQの回答が「記録と写真」（英語版は records and photos）を
        具体的に言及していない場合は、他の必須語句をすべて満たしていても
        失敗すること（合成入力から記録/写真の言及だけを一般的な語へ置換）。"""
        ja_generic = (
            "クイックスタートまたはiCloudバックアップから新しいiPhoneへ移行した場合、"
            "Private Biteのデータも通常は引き継がれます。念のため、移行前に設定画面の"
            "「データをエクスポート」で追加のバックアップを保存できます。"
        )
        with self.assertRaises(AssertionError):
            assert_iphone_migration_faq_answer_complete(ja_generic, "ja")

        en_generic = (
            "If you migrate to a new iPhone using Quick Start or an iCloud backup, "
            "Private Bite's data is usually carried over as well. As an extra "
            'precaution, you can save an additional backup before migrating by '
            'using "Export Data" in Settings.'
        )
        with self.assertRaises(AssertionError):
            assert_iphone_migration_faq_answer_complete(en_generic, "en")

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

    def test_extract_css_var_rejects_duplicate_definition(self):
        """このサイトは --primary・--surface・--background を :root で一度だけ
        定義する契約であるため、同じ変数名が（レスポンシブな上書き等で）2件
        以上見つかった場合は、どちらの値かを黙って選ばずに AssertionError を
        送出すること。1回だけ定義されている変数は引き続き取得できること。"""
        css = """
        :root {
          --primary: #0f766e;
          --surface: #ffffff;
          --background: #edf7f4;
        }

        @media (max-width: 500px) {
          :root { --primary: #cccccc; }
        }
        """
        with self.assertRaises(AssertionError):
            extract_css_var(css, "--primary")
        self.assertEqual(extract_css_var(css, "--surface"), "#ffffff")
        self.assertEqual(extract_css_var(css, "--background"), "#edf7f4")

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
            "ja_import": "../en/import/",
            "en_import": "../../import/",
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
            mailto_links = _extract_mailto_links(links)
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

    def test_mailto_extraction_is_scheme_case_insensitive(self):
        """mailto リンクの抽出が scheme の大文字小文字を問わないこと。
        href.startswith("mailto:") のような前方一致の抽出方法だと、大文字の
        "MAILTO:" が検査対象から漏れてしまうため、
        urlsplit(href).scheme.lower() == "mailto" で判定する
        _extract_mailto_links を使うことを、合成HTMLで確認する。"""
        html = (
            '<a href="mailto:privatebite.support@icloud.com?subject=x">正常</a>'
            '<a href="MAILTO:privatebite.support@icloud.com?cc=other%40example.com">不正</a>'
        )
        links = extract_links(html)
        mailto_links = _extract_mailto_links(links)
        self.assertEqual(
            len(mailto_links), 2, "mailto: と MAILTO: の両方を検出すること"
        )
        validity = [is_valid_support_mailto(link["href"]) for link in mailto_links]
        self.assertEqual(validity, [True, False])

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

    def test_top_pages_link_to_csv_import_page(self):
        """日英トップページのFAQから、それぞれの言語のCSV取込ページへの直接リンクがあること。"""
        for name in ("ja_support", "en_support"):
            with self.subTest(name=name):
                page_path = PAGES[name]
                links = extract_links(read(page_path))
                hrefs = {link["href"] for link in links}
                self.assertIn("import/", hrefs)
                resolved = _resolve_relative_link(page_path, "import/")
                self.assertTrue(resolved.is_file(), resolved)

    def test_faq_distinguishes_backup_restore_and_csv_import(self):
        """『バックアップ復元』と『CSVから記録を追加』を別目的として説明していること。"""
        ja_pairs = extract_dt_dd_pairs(read(PAGES["ja_support"]))
        ja_answer = _answer_for_question(ja_pairs, "バックアップ復元とCSV取込")
        _assert_contains_all(
            ja_answer,
            ["完全バックアップから元の状態へ戻す", "既存の記録へ追加", "CSVから記録を追加する"],
            "backup-vs-csv-ja",
        )

        en_pairs = extract_dt_dd_pairs(read(PAGES["en_support"]))
        en_answer = _answer_for_question(
            en_pairs, "restoring a backup and importing a CSV"
        )
        _assert_contains_all(
            en_answer,
            [
                "original state",
                "complete backup",
                "add rows",
                "existing records",
                "Add records from a CSV file",
            ],
            "backup-vs-csv-en",
        )

    def test_privacy_pages_disclose_csv_import_without_duplicating_frankfurter_details(
        self,
    ):
        """プライバシーページにCSV取込の端末内解析・外貨換算時の送信を追記しつつ、
        既存のFrankfurter API説明（送信内容の詳細）を重複して書き下していないこと。"""
        ja = read(PAGES["ja_privacy"])
        en = read(PAGES["en_privacy"])
        _assert_contains_all(
            ja,
            ["端末内で解析", "外部へ送信されることはありません", "外部サービス", "Frankfurter API"],
            "csv-privacy-ja",
        )
        _assert_contains_all(
            en,
            [
                "parsed entirely on your device",
                "not sent anywhere",
                "External services",
                "Frankfurter API",
            ],
            "csv-privacy-en",
        )
        # 送信内容のフル説明（日付・換算元通貨コード・換算先通貨コード）は
        # 「3. 外部サービス」に1回だけ記載し、CSV取込の追記では重複させない。
        self.assertEqual(
            ja.count("換算元通貨コード、換算先通貨コードを送信します"), 1
        )
        self.assertEqual(
            en.count(
                "source currency code, and target currency code to obtain an "
                "exchange rate"
            ),
            1,
        )

    def test_top_to_import_to_privacy_to_top_navigation_loop(self):
        """日英それぞれで トップ→CSV取込→プライバシー→トップ の導線が
        すべて実ファイルへ解決し、最終的にトップページへ戻ってくること。"""
        chains = {
            "ja": PAGES["ja_support"],
            "en": PAGES["en_support"],
        }
        for lang, start_page in chains.items():
            with self.subTest(lang=lang):
                import_path = _resolve_relative_link(start_page, "import/")
                self.assertTrue(import_path.is_file(), import_path)

                privacy_path = _resolve_relative_link(import_path, "../privacy/")
                self.assertTrue(privacy_path.is_file(), privacy_path)

                top_path = _resolve_relative_link(privacy_path, "../")
                self.assertTrue(top_path.is_file(), top_path)
                self.assertEqual(top_path, start_page.resolve())

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

    def test_extract_external_css_urls_handles_embedded_opposite_quote(self):
        """引用符で囲んだURL値の内部に反対側の引用符が含まれていても、正しく
        1件の外部URLとして抽出できること。開始引用符と異なる引用符を除外する
        文字クラス（例: [^'")]）だと、値の途中に反対側の引用符が現れた時点で
        マッチが途切れてしまうため、ダブルクォート・シングルクォート・
        引用符なしを別々の正規表現選択肢として扱う必要がある。"""
        with self.subTest(value="double-quoted url containing a single quote"):
            css = "background-image: url(\"https://cdn.example.com/o'neil.png\");"
            self.assertEqual(
                extract_external_css_urls(css),
                ["https://cdn.example.com/o'neil.png"],
            )

        with self.subTest(value="single-quoted url containing a double quote"):
            css = "background-image: url('https://cdn.example.com/a\"b.png');"
            self.assertEqual(
                extract_external_css_urls(css),
                ['https://cdn.example.com/a"b.png'],
            )


class CsvTemplateContractTest(unittest.TestCase):
    """CSVから記録を追加する機能向けの公式CSVテンプレート（日英）の契約テスト。

    アプリ側 CsvImportTemplate.build() が実際に生成したバイト列をそのまま
    downloads/ 配下へコピーしたものであることを検査する。ここでは Flutter を
    実行できないため、既知のSHA256ハッシュへピン留めすることで、アプリ生成物
    とのbytes完全一致を固定する（ハッシュはアプリ側worktree
    csv-record-import のコミット ed9bdf4 で CsvImportTemplate.build() を
    実行して得た値）。
    """

    KNOWN_SHA256 = {
        "ja": "771f4b5d6f6a6f17a269d85af21119c3417fa28f8e7e29bdf2a9eb7b0d868995",
        "en": "9de2d8beeed8df6ce7b96a3f589554d98ab87ac49ecd9fcc0928fea78158b24e",
    }

    def test_csv_template_files_exist_with_utf8_bom(self):
        for lang, path in CSV_TEMPLATES.items():
            with self.subTest(lang=lang):
                self.assertTrue(path.is_file(), path)
                raw = read_csv_bytes(path)
                self.assertEqual(raw[:3], b"\xef\xbb\xbf", "UTF-8 BOM が先頭にないこと")

    def test_csv_template_bytes_match_app_generated_output(self):
        """公開ファイルのbytesが、アプリ側 CsvImportTemplate.build() の出力
        （ブリーフに記載の既知SHA256）と完全一致すること。生成物そのものは
        アプリrepoへコミットしない。"""
        import hashlib

        for lang, path in CSV_TEMPLATES.items():
            with self.subTest(lang=lang):
                raw = read_csv_bytes(path)
                digest = hashlib.sha256(raw).hexdigest()
                self.assertEqual(digest, self.KNOWN_SHA256[lang])

    def test_csv_template_uses_crlf_and_no_trailing_newline(self):
        for lang, path in CSV_TEMPLATES.items():
            with self.subTest(lang=lang):
                raw = read_csv_bytes(path)
                self.assertIn(b"\r\n", raw)
                self.assertFalse(raw.endswith(b"\n"))

    def test_csv_template_header_is_fixed_17_column_order(self):
        expected = {"ja": JA_HEADER_LABELS, "en": EN_HEADER_LABELS}
        for lang, path in CSV_TEMPLATES.items():
            with self.subTest(lang=lang):
                rows = parse_csv_bom(path)
                header = rows[0]
                self.assertEqual(len(header), 17)
                self.assertEqual(header, expected[lang])

    def test_csv_template_headers_do_not_mix_languages(self):
        """日本語版ヘッダーに英語版のラベルが、英語版ヘッダーに日本語版の
        ラベルが混在していないこと。"""
        rows_ja = parse_csv_bom(CSV_TEMPLATES["ja"])
        rows_en = parse_csv_bom(CSV_TEMPLATES["en"])
        header_ja = rows_ja[0]
        header_en = rows_en[0]

        for label in EN_HEADER_LABELS:
            self.assertNotIn(label, header_ja)
        for label in JA_HEADER_LABELS:
            self.assertNotIn(label, header_en)

    def test_csv_template_sample_row_has_17_cells_and_required_values(self):
        for lang, path in CSV_TEMPLATES.items():
            with self.subTest(lang=lang):
                rows = parse_csv_bom(path)
                self.assertEqual(len(rows), 2, "ヘッダー1行+サンプル1行の計2行")
                sample = rows[1]
                self.assertEqual(len(sample), 17)
                for index in REQUIRED_COLUMN_INDICES:
                    self.assertTrue(
                        sample[index].strip(),
                        f"required column at index {index} must not be empty",
                    )

    def test_csv_template_sample_row_field_formats_are_valid(self):
        for lang, path in CSV_TEMPLATES.items():
            with self.subTest(lang=lang):
                rows = parse_csv_bom(path)
                sample = rows[1]
                visit_date = sample[EN_HEADERS.index("visit_date")]
                country_code = sample[EN_HEADERS.index("country_code")]
                currency_code = sample[EN_HEADERS.index("currency_code")]

                self.assertRegex(visit_date, DATE_PATTERN)
                self.assertEqual(len(country_code), 2)
                self.assertTrue(country_code.isalpha())
                self.assertEqual(country_code, country_code.upper())
                self.assertIn(currency_code, SUPPORTED_CURRENCY_CODES)

    def test_csv_template_sample_memo_with_comma_is_quoted_correctly(self):
        """メモ欄にカンマを含むサンプル行が、17セルのまま正しく1フィールドとして
        解析されること（引用符での保護が効いていることの確認）。"""
        for lang, path in CSV_TEMPLATES.items():
            with self.subTest(lang=lang):
                rows = parse_csv_bom(path)
                sample = rows[1]
                memo = sample[EN_HEADERS.index("memo")]
                self.assertIn(",", memo)
                self.assertEqual(len(sample), 17)

    def test_csv_template_ja_en_samples_represent_the_same_visit(self):
        """日英サンプル行が同じ意味のデータ（訪問グループ、日付、国、価格、
        通貨、各評価、再訪希望）を表していること。"""
        rows_ja = parse_csv_bom(CSV_TEMPLATES["ja"])
        rows_en = parse_csv_bom(CSV_TEMPLATES["en"])
        sample_ja = rows_ja[1]
        sample_en = rows_en[1]

        same_value_keys = [
            "visit_group", "visit_date", "country_code", "price",
            "currency_code", "taste_rating", "cost_rating",
            "atmosphere_rating", "service_rating",
        ]
        for key in same_value_keys:
            index = EN_HEADERS.index(key)
            with self.subTest(key=key):
                self.assertEqual(sample_ja[index], sample_en[index])

        revisit_index = EN_HEADERS.index("revisit_flag")
        self.assertEqual(sample_ja[revisit_index], "はい")
        self.assertEqual(sample_en[revisit_index], "TRUE")


class ImportPageContractTest(unittest.TestCase):
    """CSVから記録を追加する説明ページ（日英）の契約テスト。"""

    EXPECTED_H2_JA = [
        "1. テンプレートを保存",
        "2. ExcelまたはGoogleスプレッドシートで入力",
        "3. CSVとして保存",
        "4. Private Biteで取込前確認",
        "列と入力規則",
        "訪問グループの使い方",
        "重複・店舗・カテゴリ・外貨",
        "よくあるエラーと直し方",
        "プライバシー",
    ]

    EXPECTED_H2_EN = [
        "1. Save the template",
        "2. Fill it in with Excel or Google Sheets",
        "3. Save as CSV",
        "4. Review before importing into Private Bite",
        "Columns and rules",
        "Using visit groups",
        "Duplicates, restaurants, categories, and currency",
        "Common errors and fixes",
        "Privacy",
    ]

    def test_import_pages_exist(self):
        self.assertTrue(PAGES["ja_import"].is_file())
        self.assertTrue(PAGES["en_import"].is_file())

    def test_import_page_lang_title_h1(self):
        ja_html = read(PAGES["ja_import"])
        en_html = read(PAGES["en_import"])
        self.assertIn('lang="ja"', ja_html)
        self.assertIn('lang="en"', en_html)
        self.assertIn("Private Bite", extract_title(ja_html))
        self.assertIn("CSV", extract_title(ja_html))
        self.assertIn("Private Bite", extract_title(en_html))
        self.assertIn("CSV", extract_title(en_html))
        self.assertIn("<h1>CSVから記録を追加する</h1>", ja_html)
        self.assertIn("<h1>Add records from a CSV file</h1>", en_html)

    def test_import_page_heading_order(self):
        self.assertEqual(
            extract_h2_texts(read(PAGES["ja_import"])), self.EXPECTED_H2_JA
        )
        self.assertEqual(
            extract_h2_texts(read(PAGES["en_import"])), self.EXPECTED_H2_EN
        )

    def test_import_page_has_exactly_one_language_link(self):
        # test_language_links_are_mutual (SiteContractTest) covers the actual
        # href assertions; this test just confirms both pages carry exactly
        # one nav.language link (helper raises if the count is ever not 1).
        for name in ("ja_import", "en_import"):
            with self.subTest(name=name):
                links = extract_links(read(PAGES[name]))
                _language_link_href(links)

    def test_import_page_links_to_support_home_and_privacy(self):
        for name, expected_home, expected_privacy in (
            ("ja_import", "../", "../privacy/"),
            ("en_import", "../", "../privacy/"),
        ):
            with self.subTest(name=name):
                links = extract_links(read(PAGES[name]))
                hrefs = {link["href"] for link in links}
                self.assertIn(expected_home, hrefs)
                self.assertIn(expected_privacy, hrefs)

    def test_import_page_csv_download_links(self):
        ja_html = read(PAGES["ja_import"])
        en_html = read(PAGES["en_import"])
        self.assertIn('href="../downloads/private-bite-import-v1-ja.csv"', ja_html)
        self.assertIn('href="../downloads/private-bite-import-v1-en.csv"', ja_html)
        self.assertIn(
            'href="../../downloads/private-bite-import-v1-en.csv"', en_html
        )
        self.assertIn(
            'href="../../downloads/private-bite-import-v1-ja.csv"', en_html
        )

    def test_import_page_relative_links_resolve_to_existing_files(self):
        for name in ("ja_import", "en_import"):
            path = PAGES[name]
            html = read(path)
            links = extract_links(html)
            for link in links:
                href = link["href"]
                if href.startswith(("mailto:", "http://", "https://")):
                    continue
                with self.subTest(name=name, href=href):
                    resolved = _resolve_relative_link(path, href)
                    self.assertTrue(resolved.is_file(), resolved)

    def test_import_page_stylesheet_link_resolves(self):
        for name, expected in (
            ("ja_import", '<link rel="stylesheet" href="../styles.css">'),
            ("en_import", '<link rel="stylesheet" href="../../styles.css">'),
        ):
            with self.subTest(name=name):
                self.assertIn(expected, read(PAGES[name]))

    def test_import_page_table_covers_all_columns_matching_csv_headers(self):
        cases = {
            "ja_import": JA_HEADER_LABELS,
            "en_import": EN_HEADER_LABELS,
        }
        for name, labels in cases.items():
            with self.subTest(name=name):
                rows = extract_table_rows(read(PAGES[name]))
                self.assertGreaterEqual(len(rows), 18, "header row + 17 data rows")
                data_rows = rows[1:18]
                self.assertEqual([row[0] for row in data_rows], labels)

    def test_import_page_table_marks_required_columns(self):
        ja_rows = extract_table_rows(read(PAGES["ja_import"]))[1:18]
        en_rows = extract_table_rows(read(PAGES["en_import"]))[1:18]
        for i, row in enumerate(ja_rows):
            expected = "必須" if i in REQUIRED_COLUMN_INDICES else "任意"
            with self.subTest(lang="ja", index=i):
                self.assertEqual(row[1], expected)
        for i, row in enumerate(en_rows):
            expected = "Required" if i in REQUIRED_COLUMN_INDICES else "Optional"
            with self.subTest(lang="en", index=i):
                self.assertEqual(row[1], expected)

    def test_import_page_table_is_scroll_wrapped(self):
        for name in ("ja_import", "en_import"):
            with self.subTest(name=name):
                self.assertRegex(
                    read(PAGES[name]), r'<div class="table-scroll">\s*<table'
                )

    def test_stylesheet_defines_table_scroll_overflow(self):
        css = read(ROOT / "styles.css")
        match = re.search(r"\.table-scroll\s*\{([^}]*)\}", css)
        self.assertIsNotNone(match, "no .table-scroll rule found")
        self.assertIn("overflow-x", match.group(1))
        self.assertIn("auto", match.group(1))

    def test_import_page_required_three_columns_are_explicit(self):
        ja = read(PAGES["ja_import"])
        en = read(PAGES["en_import"])
        _assert_contains_all(ja, ["3つだけ", "行ごとに入力"], "required-3-ja")
        _assert_contains_all(
            en,
            ["Only three columns are required", "each row still needs"],
            "required-3-en",
        )

    def test_import_page_rating_and_date_rules(self):
        ja = read(PAGES["ja_import"])
        en = read(PAGES["en_import"])
        _assert_contains_all(
            ja, ["1.0〜5.0", "0.1刻み", "1〜5の整数", "YYYY-MM-DD"], "rules-ja"
        )
        _assert_contains_all(
            en,
            ["1.0 to 5.0", "steps of 0.1", "1 to 5", "YYYY-MM-DD"],
            "rules-en",
        )

    def test_import_page_currency_default_and_no_country_inference(self):
        ja = read(PAGES["ja_import"])
        en = read(PAGES["en_import"])
        _assert_contains_all(ja, ["既定通貨", "推測することはありません"], "currency-ja")
        _assert_contains_all(
            en, ["default currency", "never used to guess"], "currency-en"
        )

    def test_import_page_visit_group_explained_with_example(self):
        ja = read(PAGES["ja_import"])
        en = read(PAGES["en_import"])
        _assert_contains_all(
            ja, ["同じ食事で複数の料理", "空欄", "別の訪問"], "visit-group-ja"
        )
        _assert_contains_all(
            en, ["same meal", "blank", "separate visit"], "visit-group-en"
        )

    def test_import_page_append_only_disclosure(self):
        ja = read(PAGES["ja_import"])
        en = read(PAGES["en_import"])
        _assert_contains_all(
            ja, ["追加されます", "更新したり", "置き換えたり"], "append-only-ja"
        )
        _assert_contains_all(
            en,
            ["added to your existing records", "does not update", "replace"],
            "append-only-en",
        )

    def test_import_page_duplicate_default_exclusion(self):
        ja = read(PAGES["ja_import"])
        en = read(PAGES["en_import"])
        _assert_contains_all(ja, ["既定で", "除外", "含める"], "duplicate-ja")
        _assert_contains_all(en, ["excluded by default", "include"], "duplicate-en")

    def test_import_page_invalid_group_excluded_entirely(self):
        ja = read(PAGES["ja_import"])
        en = read(PAGES["en_import"])
        _assert_contains_all(ja, ["訪問グループ全体", "除外"], "invalid-group-ja")
        _assert_contains_all(
            en, ["entire visit group", "excluded"], "invalid-group-en"
        )

    def test_import_page_local_parsing_and_frankfurter_disclosure(self):
        ja = read(PAGES["ja_import"])
        en = read(PAGES["en_import"])
        _assert_contains_all(
            ja,
            ["端末内で解析", "Frankfurter", "送信することはありません"],
            "privacy-ja",
        )
        _assert_contains_all(
            en,
            ["parsed entirely on your device", "Frankfurter", "never sent"],
            "privacy-en",
        )

    def test_import_page_file_limits(self):
        ja = read(PAGES["ja_import"])
        en = read(PAGES["en_import"])
        _assert_contains_all(ja, ["10MB", "10,000行", "UTF-8"], "limits-ja")
        _assert_contains_all(en, ["10MB", "10,000", "UTF-8"], "limits-en")


if __name__ == "__main__":
    unittest.main()
