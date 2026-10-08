"""长安大学官网公开政策批量采集。

只访问 *.chd.edu.cn 的公开页面：从各部门栏目列表页出发，翻页收集文章链接，抽取正文；
正文是嵌入 PDF 的（教务处规章常见），下载 PDF 并用 pdftotext 转为文本。输出为 loader
可直接读取的 Markdown（front matter + 章节标题），以及一份 manifest.csv。

不访问信息门户、统一身份认证等需要登录的系统，不处理验证码。遵守 robots.txt，
请求之间默认间隔 1 秒。输出的是原文抽取（content_type: raw_extract），默认写入
data/public_corpus/chd_raw/，不会自动进入在线检索；进入 chd_public 前应人工核对并
整理成释义摘要。

用法（在 02_Python_Version 目录）：
    python -m src.ingestion.chd_crawler                      # 默认栏目
    python -m src.ingestion.chd_crawler --seed https://jwc.chd.edu.cn/6995/list.htm --max-pages 2
    python -m src.ingestion.chd_crawler --all --max-articles 20   # 不按标题过滤
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import urllib.robotparser
from dataclasses import dataclass, field
from datetime import date
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from src.config import settings

USER_AGENT = "CampusAIServiceCourseCrawler/1.0 (+course project; polite, public pages only)"
DEFAULT_OUT = settings.project_root / "data" / "public_corpus" / "chd_raw"

# 面向学生的政策与办事栏目。部门名写入 topic，便于检索与人工分拣。
DEFAULT_SEEDS = [
    ("教务处", "https://jwc.chd.edu.cn/6995/list.htm"),       # 规章制度
    ("教务处", "https://jwc.chd.edu.cn/7016/list.htm"),       # 学籍管理
    ("教务处", "https://jwc.chd.edu.cn/6996/list.htm"),       # 办事指南
    ("信网处", "https://xwc.chd.edu.cn/xykglzx/list.htm"),    # 校园卡管理中心
    ("信网处", "https://xwc.chd.edu.cn/wlywfwzx/list.htm"),   # 网络运维服务中心
    ("校医院", "https://xyy.chd.edu.cn/ybzc/list.htm"),       # 医保政策
    ("保卫处", "https://gac.chd.edu.cn/fwzn/list.htm"),       # 服务指南
    ("保卫处", "https://gac.chd.edu.cn/hz/list.htm"),         # 户政
    ("保卫处", "https://gac.chd.edu.cn/xngz/list.htm"),       # 校内规章
    ("学生工作部", "https://xgb.chd.edu.cn/4706/list.htm"),   # 通知公告
    ("学生工作部", "https://xgb.chd.edu.cn/4688/list.htm"),   # 资助育人
]

# 标题过滤：先排除新闻类，再要求命中政策/办事类关键词。--all 可关闭。
EXCLUDE = re.compile(
    r"召开|举办|举行|顺利|圆满|喜获|荣获|座谈|调研|访问|走访|讲堂|讲座|风采|活动|公示|名单|结果|培训会|学习教育|党支部|党委"
    # 面向辅导员、教师、职工的内部工作通知，对学生问答是噪声
    r"|晚点名|周见面|工作提示|期间.{0,6}学生工作|暑期学生工作|教育管理工作|职工|教师|动火|专项资金|责任教授|课程建设|课程评价"
)
INCLUDE = re.compile(r"办法|规定|细则|规则|条例|章程|指南|流程|须知|办理|申请|报销|政策|简介|说明|标准|方案|补办|挂失|充值|报修|重置|自助|通知")

ARTICLE_LINK = re.compile(r"/\d{4}/\d{4}/c\d+a\d+/page\.(?:htm|psp)$|/info/\d+/\d+\.htm$")
LIST_PAGE = re.compile(r"/list(\d+)\.htm$")
DATE_NEAR_TITLE = re.compile(r"(?:发布时间|发布日期|日期|时间)?[:：\s]*(20\d{2}|19\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})")
CONTENT_CLASSES = ("wp_articlecontent", "v_news_content", "Article_Content")
BLOCK_TAGS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table", "section"}
SKIP_TAGS = {"script", "style", "noscript", "iframe"}
CJK = "\u4e00-\u9fff"


# ---------------------------------------------------------------- HTML 解析

class _LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            title = re.sub(r"\s+", " ", "".join(self._text)).strip()
            self.links.append((self._href, title))
            self._href = None


class _ArticleParser(HTMLParser):
    """抽取标题、正文容器内的分行文本，以及 PDF 附件地址。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.page_title = ""
        self.lines: list[str] = []
        self.pdfs: list[str] = []
        self.meta_text: list[str] = []
        self._in_title = self._in_h1 = self._in_meta = False
        self._depth = 0          # 正文容器内的 div 深度；0 表示不在正文中
        self._skip = 0
        self._cell: list[str] = []
        self._buffer: list[str] = []

    def _flush(self):
        text = re.sub(r"[ \t\u3000\xa0]+", " ", "".join(self._buffer)).strip()
        if text:
            self.lines.append(text)
        self._buffer = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class") or ""
        if tag in SKIP_TAGS:
            self._skip += 1
        if tag == "title":
            self._in_title = True
        if tag == "h1" and "arti_title" in cls:
            self._in_h1 = True
        if "arti_metas" in cls or "arti_update" in cls:
            self._in_meta = True
        if a.get("pdfsrc"):
            self.pdfs.append(a["pdfsrc"])
        if tag == "a" and (a.get("href") or "").lower().endswith(".pdf"):
            self.pdfs.append(a["href"])
        if tag == "div":
            if self._depth:
                self._depth += 1
            elif any(c in cls.split() or c == cls for c in CONTENT_CLASSES):
                self._depth = 1
        if self._depth and tag in BLOCK_TAGS:
            self._flush()
        if self._depth and tag in {"td", "th"}:
            self._cell = []

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False
        if tag == "h1":
            self._in_h1 = False
        if tag in {"p", "div"}:
            self._in_meta = False
        if self._depth and tag in {"td", "th"}:
            self._buffer.append(" | ")
        if self._depth and tag in BLOCK_TAGS:
            self._flush()
        if tag == "div" and self._depth:
            self._depth -= 1

    def handle_data(self, data):
        if self._skip:
            return
        if self._in_title:
            self.page_title += data
        if self._in_h1:
            self.title += data
        if self._in_meta:
            self.meta_text.append(data)
        if self._depth:
            self._buffer.append(data)


@dataclass
class Article:
    url: str
    title: str
    source_date: str
    body: str                      # Markdown 正文（不含一级标题）
    attachments: list[str] = field(default_factory=list)
    from_pdf: bool = False


def parse_list(html: str, base_url: str) -> tuple[list[tuple[str, str]], int]:
    """返回 (文章链接与标题, 总页数)。"""
    parser = _LinkParser()
    parser.feed(html)
    seen, articles = set(), []
    for href, title in parser.links:
        url = urljoin(base_url, unescape(href))
        if ARTICLE_LINK.search(urlsplit(url).path) and url not in seen:
            seen.add(url)
            articles.append((url, title))
    pages = re.search(r'class="all_pages"[^>]*>\s*(\d+)', html)
    return articles, int(pages.group(1)) if pages else 1


def list_page_url(first: str, page: int) -> str:
    return first if page == 1 else re.sub(r"/list\d*\.htm$", f"/list{page}.htm", first)


def _date_from(text: str) -> str:
    m = DATE_NEAR_TITLE.search(text)
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else "unknown"


def html_lines_to_markdown(lines: list[str]) -> str:
    """把正文分行转为 Markdown：短的“一、xxx”类行视为二级标题，供分块器按章节切分。"""
    out = []
    for line in lines:
        line = line.strip(" |")
        if not line:
            continue
        if len(line) <= 30 and re.match(rf"^(?:[一二三四五六七八九十]+、|第[{CJK}0-9]+[章节部分])", line):
            out.append(f"## {line}")
        else:
            out.append(line)
    return "\n".join(out)


def parse_article(html: str, url: str) -> Article:
    parser = _ArticleParser()
    parser.feed(html)
    parser._flush()
    title = re.sub(r"\s+", " ", parser.title or parser.page_title).strip()
    meta = " ".join(parser.meta_text)
    # 日期优先取文章元信息；没有时在正文前几行找，避免把正文里的政策日期当作发布日期。
    source_date = _date_from(meta)
    if source_date == "unknown" and title:
        # 部分模板把日期直接写在标题后面（如“2026-06-22 点击：75次”）。
        plain = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html)))
        # 只接受紧跟标题的 YYYY-MM-DD，避免把导航或正文里的其他日期当作发布日期。
        for m in re.finditer(re.escape(title[:20]), plain):
            found = re.match(r"[^0-9]{0,12}?\s*(20\d{2})-(\d{2})-(\d{2})", plain[m.end(): m.end() + 40])
            if found:
                source_date = "-".join(found.groups())
                break
    lines = [l for l in parser.lines if l != title]
    pdfs = list(dict.fromkeys(urljoin(url, p) for p in parser.pdfs))
    return Article(url, title, source_date, html_lines_to_markdown(lines), pdfs)


# ---------------------------------------------------------------- PDF 文本整理

def clean_pdf_text(raw: str) -> str:
    """pdftotext 输出整理：去页码、合并断行、把“第X章”转为二级标题。"""
    paragraphs: list[str] = []
    current = ""
    numerals = "一二三四五六七八九十百0-9"
    starts = re.compile(rf"^(?:第[{numerals}]+[章条]|[（(][{numerals}]+[)）]|[一二三四五六七八九十]+、|\d+[.、．]|附件|抄送)")
    for raw_line in raw.replace("\f", "\n").splitlines():
        line = raw_line.strip()
        if not line or re.fullmatch(r"[-—–\s]*\d+[-—–\s]*", line):
            continue
        # 公文常见“长 安 大 学 文 件”“2023 年 11 月”式空格
        line = re.sub(rf"(?<=[{CJK}0-9])\s+(?=[{CJK}])|(?<=[{CJK}])\s+(?=[0-9])", "", line)
        if starts.match(line) and current:
            paragraphs.append(current)
            current = line
        else:
            current += line
        if current.endswith(("。", "：", "；", "！", "？")) or (len(current) <= 30 and re.match(rf"^第[{CJK}0-9]+章", current)):
            paragraphs.append(current)
            current = ""
    if current:
        paragraphs.append(current)
    out = []
    for p in paragraphs:
        m = re.match(rf"^(第[{numerals}]+章)\s*(.{{0,24}})$", p)
        out.append(f"## {m.group(1)} {m.group(2)}".strip() if m else p)
    return "\n".join(out)


def looks_like_diagram(text: str) -> bool:
    """流程图、表格类 PDF 抽出的文字几乎没有句读，拼起来是乱序词组，不应入库。"""
    compact = re.sub(r"\s+", "", text)
    if len(compact) < 100:
        return False
    return sum(compact.count(c) for c in "。；：，") / len(compact) < 0.01


def pdf_issue_date(text: str) -> str:
    """公文日期：优先取“××年×月×日印发”，否则取开头 600 字内的第一个完整日期。"""
    pattern = r"(20\d{2}|19\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"
    m = re.search(pattern + r"\s*印发", text) or re.search(pattern, text[:600])
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else "unknown"


def pdf_to_text(data: bytes) -> str | None:
    tool = shutil.which("pdftotext")
    if not tool:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "in.pdf"
        src.write_bytes(data)
        result = subprocess.run([tool, "-enc", "UTF-8", str(src), "-"], capture_output=True, timeout=60)
    return result.stdout.decode("utf-8", "replace") if result.returncode == 0 else None


# ---------------------------------------------------------------- 网络访问

class Fetcher:
    def __init__(self, delay: float, insecure: bool = False, timeout: int = 20):
        self.delay = delay
        self.timeout = timeout
        self.context = ssl.create_default_context()
        if insecure:
            self.context.check_hostname = False
            self.context.verify_mode = ssl.CERT_NONE
        self._last = 0.0
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    @staticmethod
    def allowed_host(url: str) -> bool:
        host = urlsplit(url).hostname or ""
        blocked = {"portal.chd.edu.cn", "ids.chd.edu.cn", "authserver.chd.edu.cn", "mail.chd.edu.cn"}
        return urlsplit(url).scheme in {"http", "https"} and host.endswith(".chd.edu.cn") and host not in blocked

    def _robots_ok(self, url: str) -> bool:
        parts = urlsplit(url)
        key = f"{parts.scheme}://{parts.netloc}"
        if key not in self._robots:
            parser = urllib.robotparser.RobotFileParser()
            try:
                body = self._raw(key + "/robots.txt", respect_robots=False)
                text = body.decode("utf-8", "replace")
                # 部分站点对缺失的 robots.txt 返回 200 的 HTML 错误页，按“无限制”处理。
                parser.parse([] if "<html" in text.lower() else text.splitlines())
                self._robots[key] = parser
            except urllib.error.HTTPError:
                self._robots[key] = None
            except (urllib.error.URLError, OSError):
                self._robots[key] = None
        parser = self._robots[key]
        return parser is None or parser.can_fetch(USER_AGENT, url)

    def _raw(self, url: str, respect_robots: bool = True) -> bytes:
        if not self.allowed_host(url):
            raise PermissionError(f"不在允许范围内的地址：{url}")
        if respect_robots and not self._robots_ok(url):
            raise PermissionError(f"robots.txt 不允许：{url}")
        wait = self.delay - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout, context=self.context) as response:
                final = response.geturl()
                if not self.allowed_host(final):
                    raise PermissionError(f"重定向到允许范围外：{final}")
                return response.read(20_000_001)[:20_000_000]
        finally:
            self._last = time.monotonic()

    def text(self, url: str) -> str:
        data = self._raw(url)
        for encoding in ("utf-8", "gb18030"):
            try:
                return data.decode(encoding)
            except UnicodeDecodeError:
                continue
        return data.decode("utf-8", "replace")

    def binary(self, url: str) -> bytes:
        return self._raw(url)


# ---------------------------------------------------------------- 采集流程

def wanted(title: str, take_all: bool) -> bool:
    return take_all or (not EXCLUDE.search(title) and bool(INCLUDE.search(title)))


def normalize(text: str) -> str:
    return re.sub(r"\s+", "", text)


def render(doc_id: str, article: Article, department: str, retrieved: str) -> str:
    title = article.title.replace("\n", " ")
    note = f"长安大学{department}官网原文抽取{'（正文来自PDF附件）' if article.from_pdf else ''}；未经人工核对，使用前请核对原文"
    return (
        f"---\nid: {doc_id}\ntitle: {title}\ntopic: {department}\nschool: 长安大学\nurl: {article.url}\n"
        f"source_date: {article.source_date}\nretrieved_at: {retrieved}\ncontent_type: raw_extract\nsource: {note}\n---\n"
        f"# {title}\n{article.body}\n"
    )


def crawl(seeds, out_dir: Path, max_pages: int, max_articles: int, take_all: bool, fetcher: Fetcher,
          refresh: bool = False, log=print) -> list[dict[str, str]]:
    docs_dir = out_dir / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.csv"
    previous = {}
    if manifest_path.exists() and not refresh:
        with manifest_path.open(encoding="utf-8") as f:
            previous = {row["url"]: row for row in csv.DictReader(f)}
    rows: dict[str, dict[str, str]] = {}
    hashes = {row["sha256"]: row["id"] for row in previous.values() if row.get("sha256")}
    retrieved = date.today().isoformat()
    saved = 0

    for department, seed in seeds:
        queue: list[tuple[str, str]] = []
        try:
            first_html = fetcher.text(seed)
        except (urllib.error.URLError, OSError, PermissionError) as exc:
            log(f"[列表失败] {seed}: {exc}")
            continue
        links, total = parse_list(first_html, seed)
        queue.extend(links)
        for page in range(2, min(total, max_pages) + 1):
            try:
                queue.extend(parse_list(fetcher.text(list_page_url(seed, page)), seed)[0])
            except (urllib.error.URLError, OSError, PermissionError) as exc:
                log(f"[翻页失败] {seed} 第{page}页: {exc}")
        log(f"[栏目] {department} {seed}：{len(queue)} 篇（共 {total} 页，读取 {min(total, max_pages)} 页）")

        for url, list_title in queue:
            if saved >= max_articles:
                break
            if url in rows:
                continue
            row = {"id": "", "url": url, "title": list_title, "department": department, "source_date": "",
                   "status": "", "chars": "0", "attachments": "", "sha256": "", "file": ""}
            rows[url] = row
            if url in previous and previous[url]["status"] in {"saved", "duplicate", "filtered", "too_short", "diagram", "external"}:
                rows[url] = previous[url]
                continue
            if not wanted(list_title, take_all):
                row["status"] = "filtered"
                continue
            if not fetcher.allowed_host(url):
                row["status"] = "external"
                continue
            try:
                article = parse_article(fetcher.text(url), url)
                if not article.title:
                    article.title = list_title
                if len(normalize(article.body)) < 200 and article.attachments:
                    texts = []
                    for pdf in article.attachments:
                        text = pdf_to_text(fetcher.binary(pdf))
                        if text is None:
                            row["status"] = "pdf_skipped"  # 未安装 pdftotext
                            break
                        if article.source_date == "unknown":
                            article.source_date = pdf_issue_date(text)
                        cleaned = clean_pdf_text(text)
                        if cleaned not in texts:  # 同一 PDF 常被 pdfsrc 和下载链接各引用一次
                            texts.append(cleaned)
                    if texts:
                        article.body = "\n".join(texts)
                        article.from_pdf = True
                        if looks_like_diagram(article.body):
                            row.update(status="diagram", title=article.title)  # 流程图类 PDF，文字无法还原语序
                            continue
                row["attachments"] = " ".join(article.attachments)
            except (urllib.error.URLError, OSError, PermissionError, subprocess.SubprocessError) as exc:
                row["status"] = f"error: {type(exc).__name__}"
                log(f"[失败] {url}: {exc}")
                continue
            if row["status"] == "pdf_skipped":
                log(f"[跳过] 需要 pdftotext 才能解析 PDF：{url}")
                continue
            body = normalize(article.body)
            row.update(title=article.title, source_date=article.source_date, chars=str(len(body)))
            if len(body) < 80:
                row["status"] = "too_short"   # 多为正文是图片或只有附件下载链接
                continue
            digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
            row["sha256"] = digest
            if digest in hashes:
                row.update(status="duplicate", id=hashes[digest])  # 同一文章挂在多个栏目下
                continue
            doc_id = "RAW" + digest[:8].upper()
            path = docs_dir / f"{doc_id}.md"
            path.write_text(render(doc_id, article, department, retrieved), encoding="utf-8")
            hashes[digest] = doc_id
            row.update(id=doc_id, status="saved", file=str(path.relative_to(out_dir)))
            saved += 1
            log(f"[保存] {doc_id} {article.source_date} {article.title}（{len(body)} 字{'，PDF' if article.from_pdf else ''}）")

    merged = {**previous, **rows}
    with manifest_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "url", "title", "department", "source_date", "status",
                                               "chars", "attachments", "sha256", "file"])
        writer.writeheader()
        writer.writerows(merged.values())
    return list(rows.values())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="采集长安大学官网公开政策与办事指南（不访问需要登录的系统）")
    parser.add_argument("--seed", action="append", metavar="URL", help="栏目列表页地址，可重复；不填使用内置栏目")
    parser.add_argument("--department", default="官网", help="与 --seed 搭配使用的部门名")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"输出目录（默认 {DEFAULT_OUT}）")
    parser.add_argument("--max-pages", type=int, default=5, help="每个栏目最多读取的列表页数")
    parser.add_argument("--max-articles", type=int, default=200, help="本次最多保存的文章数")
    parser.add_argument("--delay", type=float, default=1.0, help="请求间隔秒数，不建议低于 0.5")
    parser.add_argument("--all", action="store_true", help="不按标题关键词过滤（会包含新闻）")
    parser.add_argument("--refresh", action="store_true", help="忽略已有 manifest，重新抓取")
    parser.add_argument("--insecure", action="store_true", help="跳过 HTTPS 证书校验（仅在本机证书库缺失时使用）")
    args = parser.parse_args(argv)
    if args.delay < 0.5:
        parser.error("--delay 不能低于 0.5 秒")
    seeds = [(args.department, s) for s in args.seed] if args.seed else DEFAULT_SEEDS
    for _, seed in seeds:
        if not Fetcher.allowed_host(seed):
            parser.error(f"只允许 *.chd.edu.cn 的公开页面：{seed}")
    if not shutil.which("pdftotext"):
        print("提示：未找到 pdftotext（macOS 可用 brew install poppler 安装），PDF 正文的规章将被跳过。", file=sys.stderr)
    rows = crawl(seeds, args.out, args.max_pages, args.max_articles, args.all, Fetcher(args.delay, args.insecure),
                 refresh=args.refresh)
    counts: dict[str, int] = {}
    for row in rows:
        key = row["status"].split(":")[0]
        counts[key] = counts.get(key, 0) + 1
    print("完成：" + "，".join(f"{k} {v}" for k, v in sorted(counts.items())) + f"；输出目录 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
