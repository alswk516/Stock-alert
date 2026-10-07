"""매일 아침 브리핑 텍스트 생성 (텔레그램용): 국제 시황 분석뉴스 3건 + 보유종목 종가."""
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

import requests

UA = {"User-Agent": "Mozilla/5.0"}

# 국제 시황 관련 RSS (config.json 의 "news_rss" 로 바꿀 수 있음). 앞에서부터 모아서 3건 선택.
DEFAULT_RSS = [
    "https://www.hankyung.com/feed/international",
    "https://www.hankyung.com/feed/finance",
    "https://www.fnnews.com/rss/r20/fn_realnews_stock.xml",
    "https://news.google.com/rss/search?q=%EB%89%B4%EC%9A%95%EC%A6%9D%EC%8B%9C+OR+%EA%B5%AD%EC%A0%9C%EC%9C%A0%EA%B0%80+OR+%EC%97%B0%EC%A4%80&hl=ko&gl=KR&ceid=KR:ko",
]
KEYWORDS = ["뉴욕", "미국", "연준", "Fed", "나스닥", "S&P", "다우", "유가", "달러", "중국", "금리", "글로벌", "월가", "증시"]

INDEXES = [
    ("코스피", "^KS11", 2, ""),
    ("코스닥", "^KQ11", 2, ""),
    ("S&P500", "^GSPC", 2, ""),
    ("나스닥", "^IXIC", 2, ""),
    ("원/달러", "KRW=X", 1, "원"),
    ("WTI", "CL=F", 2, "$"),
]


def quote(sym):
    """(현재/종가, 전일대비%) 반환. 실패 시 None."""
    try:
        r = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=5d&interval=1d",
            headers=UA, timeout=10,
        )
        m = r.json()["chart"]["result"][0]["meta"]
        px, prev = m["regularMarketPrice"], m.get("chartPreviousClose") or m.get("previousClose")
        return px, (px - prev) / prev * 100 if prev else 0.0
    except Exception:
        return None


def arrow(p):
    return "▲" if p > 0 else ("▼" if p < 0 else "-")


def market_lines():
    out = []
    for name, sym, nd, unit in INDEXES:
        q = quote(sym)
        if q:
            px, pct = q
            out.append(f"{name} {px:,.{nd}f}{unit} {arrow(pct)}{abs(pct):.1f}%")
    return out


def holdings_lines(watchlist):
    out = []
    for w in watchlist:
        mk = w.get("market", "KR")
        sym = w.get("yahoo") or (w["symbol"] + ".KS" if mk == "KR" else w["symbol"])
        q = quote(sym)
        if q:
            px, pct = q
            cur = f"{px:,.0f}원" if mk == "KR" else f"${px:,.2f}"
            out.append(f'{w["name"]} {cur} ({arrow(pct)}{abs(pct):.1f}%)')
    return out


def domain(link):
    try:
        return urlparse(link).netloc.replace("www.", "")
    except Exception:
        return ""


def fetch_feed(url):
    try:
        r = requests.get(url, headers=UA, timeout=10)
        r.raise_for_status()
        items = []
        for it in ET.fromstring(r.content).findall(".//item")[:15]:
            title = re.sub(r"\s+", " ", (it.findtext("title") or "").strip())
            link = (it.findtext("link") or "").strip()
            src = it.find("source")
            dom = domain(src.get("url")) if src is not None and src.get("url") else domain(link)
            if title and link:
                items.append((title, link, dom))
        return items
    except Exception:
        return []


def top_news(rss_urls, n=3):
    """국제/해외 시황 키워드가 든 기사를 우선으로 중복 없이 n건."""
    pool, seen = [], set()
    for url in rss_urls or DEFAULT_RSS:
        for t, l, d in fetch_feed(url):
            key = t[:25]
            if key not in seen:
                seen.add(key)
                pool.append((t, l, d))
        if len(pool) >= 30:
            break
    pool.sort(key=lambda x: 0 if any(k in x[0] for k in KEYWORDS) else 1)  # 정렬은 안정적: 원래 순서 유지
    return pool[:n]


def build_text(cfg, now):
    parts = [f"📊 증시 브리핑 {now:%m/%d(%a)}"]
    m = market_lines()
    if m:
        parts.append("\n".join(m))
    h = holdings_lines(cfg["watchlist"])
    if h:
        parts.append("💼 보유종목 종가\n" + "\n".join(h))
    news = top_news(cfg.get("news_rss"), 3)
    if news:
        lines = ["📰 국제 시황 뉴스"]
        for i, (t, l, d) in enumerate(news, 1):
            lines.append(f"{i}. {t}\n   {d} · {l}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts) if len(parts) > 1 else ""
