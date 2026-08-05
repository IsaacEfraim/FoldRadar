# -*- coding: utf-8 -*-
"""FoldRadar daily news builder.

Pulls RSS/Atom feeds (title + description) from foldable-focused creators and
phone-news sites, then writes a FULL original article per story in FoldRadar's
voice — headline plus several paragraphs — grounded in the feed's own text.
Sources are credited by name (no outbound links); every article ends with a CTA
into our own pages so readers stay on-site.

Writing prefers the Gemini API (free tier) via GEMINI_API_KEY, falls back to
OpenAI via OPENAI_API_KEY; with no key it renders headline + feed snippet so
the page still builds. Run twice daily by GitHub Actions.
"""
import datetime
import html
import json
import os
import re
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from inline_css import inline_html

ROOT = Path(__file__).resolve().parent.parent

# (label, url, kind, filtered)  — filtered=False for foldable-only channels
FEEDS = [
    ("Shane Craig", "https://www.youtube.com/feeds/videos.xml?channel_id=UCCKQStaJH5LuMGHHeM9Y-OA", "video", False),
    ("Average Dad", "https://www.youtube.com/feeds/videos.xml?channel_id=UCEz5Ci0AKKxQvVwS8E88VUg", "video", True),
    ("GSMArena", "https://www.gsmarena.com/rss-news-reviews.php3", "news", True),
    ("9to5Google", "https://9to5google.com/feed/", "news", True),
    ("MacRumors", "https://feeds.macrumors.com/MacRumors-All", "news", True),
    ("Android Authority", "https://www.androidauthority.com/feed/", "news", True),
]

KEYWORDS = re.compile(
    r"fold|flip|razr|hinge|flex window|book-style|clamshell|tri-fold|trifold",
    re.IGNORECASE,
)
NS = {"atom": "http://www.w3.org/2005/Atom", "media": "http://search.yahoo.com/mrss/"}

# CTAs rotated across articles so every story funnels into our own pages
CTAS = [
    ("/iphone-fold", "Read the iPhone Fold tracker"),
    ("/which-foldable", "Which foldable fits you? Take the quiz"),
    ("/best-foldable-phones", "See the best foldables you can buy"),
    ("/news#launch-alert", "Get the iPhone Fold launch alert"),
    ("/iphone-fold-vs-galaxy-z-fold", "iPhone Fold vs Galaxy Z Fold"),
]

ARTICLE_COUNT = 10


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "FoldRadarBot/1.0 (+https://foldradar.com)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def clean_text(s, limit=500):
    """Strip tags/entities/whitespace from a feed description."""
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:limit]


def parse_feed(label, url, kind, filtered):
    items = []
    try:
        root = ET.fromstring(fetch(url))
    except Exception as e:
        print(f"!! {label}: {e}")
        return items
    if root.tag.endswith("feed"):  # Atom (YouTube)
        for entry in root.findall("atom:entry", NS):
            title = (entry.findtext("atom:title", "", NS) or "").strip()
            date = (entry.findtext("atom:published", "", NS) or "")[:10]
            group = entry.find("media:group", NS)
            desc = clean_text(group.findtext("media:description", "", NS) if group is not None else "")
            items.append({"title": title, "desc": desc, "date": date, "source": label, "kind": kind})
    else:  # RSS
        for it in root.iter("item"):
            title = (it.findtext("title") or "").strip()
            desc = clean_text(it.findtext("description") or "")
            pub = it.findtext("pubDate") or ""
            try:
                date = datetime.datetime.strptime(pub[5:16], "%d %b %Y").strftime("%Y-%m-%d")
            except ValueError:
                date = ""
            items.append({"title": title, "desc": desc, "date": date, "source": label, "kind": kind})
    if filtered:
        items = [i for i in items if KEYWORDS.search(i["title"])]
    return items[:8]


# ---------------- article writing ----------------

def _prompt(items):
    blocks = []
    for n, i in enumerate(items):
        blocks.append(f'STORY {n+1} (source: {i["source"]})\nTitle: {i["title"]}\nSnippet: {i["desc"] or "(none)"}')
    return (
        "You are the staff writer of FoldRadar, an independent site covering foldable "
        "phones. For EACH story below, write a full short news article for our readers:\n"
        "- an original headline (do not copy the source title's phrasing)\n"
        "- exactly 3 paragraphs of 50-80 words each: what happened, the details/context, "
        "and why it matters for foldable buyers\n"
        "Rules: use ONLY facts present in the title/snippet plus widely-known background "
        "about the products; never invent specs, prices, dates or quotes. Attribute the "
        "reporting naturally once per article (e.g. 'according to GSMArena' or 'in his "
        "latest video, Shane Craig...'). Neutral, clear, conversational tech-news tone. "
        "No links, no markdown.\n"
        'Return ONLY JSON: {"articles": [{"headline": "...", "body": ["p1", "p2", "p3"]}, ...]} '
        "in the same order as the stories.\n\n" + "\n\n".join(blocks)
    )


def _gemini(prompt, key):
    model = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.65, "responseMimeType": "application/json"},
    }).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read())
    return data["candidates"][0]["content"]["parts"][0]["text"]


def _openai(prompt, key):
    body = json.dumps({
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.65,
        "response_format": {"type": "json_object"},
    }).encode()
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions", data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())["choices"][0]["message"]["content"]


def write_articles(items):
    """Return list of {'headline': str, 'body': [p, ...]} aligned with items."""
    fallback = [{"headline": i["title"], "body": [i["desc"] or "Details are still emerging on this one."]} for i in items]
    if not items:
        return []
    gkey = os.environ.get("GEMINI_API_KEY", "").strip()
    okey = os.environ.get("OPENAI_API_KEY", "").strip()
    if not gkey and not okey:
        print("   (no LLM key — publishing headlines + snippets)")
        return fallback
    provider = "Gemini" if gkey else "OpenAI"
    try:
        content = _gemini(_prompt(items), gkey) if gkey else _openai(_prompt(items), okey)
        out = json.loads(content).get("articles", [])
        result = []
        for n, i in enumerate(items):
            a = out[n] if n < len(out) and isinstance(out[n], dict) else {}
            head = (a.get("headline") or "").strip() or i["title"]
            body = [p.strip() for p in a.get("body", []) if isinstance(p, str) and p.strip()]
            result.append({"headline": head, "body": body or fallback[n]["body"]})
        print(f"   wrote {len(result)} full articles via {provider}")
        return result
    except Exception as e:
        print(f"   ({provider} article writing failed: {str(e)[:90]} — headlines only)")
        return fallback


# ---------------- page build ----------------

def build():
    all_items = []
    for feed in FEEDS:
        got = parse_feed(*feed)
        print(f"   {feed[0]}: {len(got)} items")
        all_items.extend(got)

    all_items.sort(key=lambda i: i["date"], reverse=True)
    stories = all_items[:ARTICLE_COUNT]
    articles = write_articles(stories)

    today = datetime.date.today()
    updated_human = today.strftime("%B %d, %Y")

    def render(item, art, idx):
        cta_url, cta_label = CTAS[idx % len(CTAS)]
        head = html.escape(art["headline"])
        paras = "\n".join(f"      <p>{html.escape(p)}</p>" for p in art["body"])
        return (
            f'    <article class="newsart" id="s{idx+1}">\n'
            f'      <h2>{head}</h2>\n'
            f'      <p class="digest-meta"><span class="via">via {html.escape(item["source"])}</span>'
            f'<span class="feedmeta">{item["date"]}</span></p>\n'
            f"{paras}\n"
            f'      <p><a class="buylink" href="{cta_url}">{html.escape(cta_label)} →</a></p>\n'
            f'    </article>'
        )

    articles_html = "\n".join(render(it, a, n) for n, (it, a) in enumerate(zip(stories, articles))) \
        or '    <p>No fresh foldable stories today — check back tomorrow.</p>'

    # Structured data for the feed. The page was previously a bare WebPage, so
    # none of the day's articles were machine-readable — nothing for Top stories,
    # nothing for an assistant to cite. Each story becomes a NewsArticle in an
    # ItemList, credited to the outlet it came from.
    org_node = {
        "@type": "Organization",
        "@id": "https://foldradar.com/#org",
        "name": "FoldRadar",
        "url": "https://foldradar.com/",
        "logo": {
            "@type": "ImageObject",
            "url": "https://foldradar.com/og/logo.png",
            "width": 512,
            "height": 512,
        },
        "description": "Independent tracker for foldable phones: iPhone Fold, "
                       "Galaxy Z Fold, Pixel Fold and more.",
    }

    collection_node = {
        "@type": "CollectionPage",
        "name": "Foldable news today — daily articles",
        "url": "https://foldradar.com/news",
        "mainEntityOfPage": "https://foldradar.com/news",
        "dateModified": today.isoformat(),
        "inLanguage": "en",
        "publisher": {"@id": "https://foldradar.com/#org"},
        "mainEntity": {
            "@type": "ItemList",
            "numberOfItems": len(articles),
            "itemListElement": [
                {
                    "@type": "ListItem",
                    "position": n + 1,
                    "item": {
                        "@type": "NewsArticle",
                        "headline": art["headline"][:110],
                        "description": (art["body"][0][:200] if art["body"] else ""),
                        "url": f"https://foldradar.com/news#s{n + 1}",
                        "mainEntityOfPage": f"https://foldradar.com/news#s{n + 1}",
                        "datePublished": item["date"] or today.isoformat(),
                        "dateModified": today.isoformat(),
                        "articleSection": "Foldable phones",
                        "inLanguage": "en",
                        "image": "https://foldradar.com/og/news.png",
                        "author": {"@id": "https://foldradar.com/#org"},
                        "publisher": {"@id": "https://foldradar.com/#org"},
                        "sourceOrganization": {
                            "@type": "Organization",
                            "name": item["source"],
                        },
                    },
                }
                for n, (item, art) in enumerate(zip(stories, articles))
            ],
        },
    }

    news_ld = json.dumps({
        "@context": "https://schema.org",
        "@graph": [org_node, collection_node],
    }, ensure_ascii=False, indent=2)

    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Foldable News Today — Daily Articles | FoldRadar</title>
<meta name="description" content="Today's foldable phone news as full articles in FoldRadar's words: iPhone Fold rumors, Galaxy Z Fold and Razr coverage, credited to the source. Updated {updated_human}.">
<link rel="canonical" href="https://foldradar.com/news">
<meta name="robots" content="max-image-preview:large, max-snippet:-1, max-video-preview:-1">
<meta property="og:site_name" content="FoldRadar">
<meta property="og:locale" content="en_US">
<meta property="og:type" content="website">
<meta property="og:title" content="Foldable News Today — Daily Articles">
<meta property="og:description" content="The day's foldable stories as full articles, written by FoldRadar. Updated daily.">
<meta property="og:url" content="https://foldradar.com/news">
<meta property="og:image" content="https://foldradar.com/og/news.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="FoldRadar — Foldable News Today — Daily Articles">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="Foldable News Today — Daily Articles">
<meta name="twitter:description" content="The day's foldable stories as full articles, written by FoldRadar. Updated daily.">
<meta name="twitter:image" content="https://foldradar.com/og/news.png">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="preload" href="/fonts/Inter-var.woff2" as="font" type="font/woff2" crossorigin>
<link rel="preload" href="/fonts/SpaceGrotesk-var.woff2" as="font" type="font/woff2" crossorigin>
<link rel="stylesheet" href="/style.css">
<script type="application/ld+json">
{news_ld}
</script>
<script defer src="/_vercel/insights/script.js"></script>
</head>
<body>
<header class="site">
  <nav class="nav">
    <a class="logo" href="/">Fold<span>Radar</span></a>
    <a href="/iphone-fold">iPhone Fold</a>
    <a href="/news">News</a>
    <a href="/which-foldable">Quiz</a>
    <a href="/best-foldable-phones">Best foldables</a>
    <a class="lang" href="/de" lang="de">Deutsch</a>
  </nav>
</header>
<main>
  <p class="crumbs"><a href="/">FoldRadar</a> › News</p>
  <h1>Foldable news today</h1>
  <p class="updated">Auto-updated daily · Last refresh: {updated_human}</p>
  <p class="lead">The day's foldable stories — iPhone Fold, Galaxy Z Fold, Razr and everything with a hinge — written up in full by FoldRadar and credited to the reporters and creators who broke them.</p>

  <div class="newsfeed">
{articles_html}
  </div>

  <div class="notify" id="launch-alert">
    <h2>One email when the iPhone Fold is announced</h2>
    <p>Skip the daily checking — date, price, pre-order links, once.</p>
    <form action="https://formsubmit.co/itzike25@gmail.com" method="POST">
      <input type="hidden" name="_subject" value="FoldRadar launch-alert signup">
      <input type="hidden" name="_next" value="https://foldradar.com/thanks">
      <input type="hidden" name="_captcha" value="false">
      <input type="email" name="email" required placeholder="you@example.com" aria-label="Email address">
      <button class="btn" type="submit">Notify me</button>
    </form>
  </div>

  <p class="disclosure">Articles are written by FoldRadar from public reporting and credited to the outlet or creator named — GSMArena, MacRumors, 9to5Google, Android Authority, Shane Craig and Average Dad. We don't republish their text.</p>
</main>
<footer class="site">
  <div class="inner">
    <p><strong>FoldRadar</strong> — independent foldable phone tracker. Not affiliated with Apple, Samsung, Google or Honor. All trademarks belong to their owners.</p>
    <p>Some outbound links may be affiliate links; purchases through them can earn us a commission at no cost to you.</p>
    <p><a href="/about">About</a> · <a href="/privacy">Privacy</a> · <a href="/de" lang="de">Deutsch</a></p>
  </div>
</footer>
</body>
</html>
"""
    css = (ROOT / "style.css").read_text(encoding="utf-8")
    page = inline_html(page, css)
    (ROOT / "news.html").write_text(page, encoding="utf-8", newline="\n")
    print(f"news.html written: {len(stories)} full articles")

    # --- homepage strip: top 3 article headlines, linking to their anchors ---
    strip_lines = []
    for n, (item, art) in enumerate(list(zip(stories, articles))[:3]):
        h = html.escape(art["headline"])
        src = html.escape(item["source"])
        strip_lines.append(
            f'      <li><a class="ns-item" href="/news#s{n+1}">{h}</a>'
            f'<span class="feedmeta">via {src}</span></li>'
        )
    strip = "\n".join(strip_lines) or '      <li><a class="ns-item" href="/news">See today\'s foldable news →</a></li>'

    index_path = ROOT / "index.html"
    idx = index_path.read_text(encoding="utf-8")
    start, end = "<!-- NEWS-STRIP:START -->", "<!-- NEWS-STRIP:END -->"
    if start in idx and end in idx:
        pre, rest = idx.split(start, 1)
        _, post = rest.split(end, 1)
        updated = pre + start + "\n" + strip + "\n" + end + post
        index_path.write_text(inline_html(updated, css), encoding="utf-8", newline="\n")
        print("index.html strip updated: 3 items")
    else:
        print("!! NEWS-STRIP markers not found in index.html — strip skipped")

    # --- sitemap: bump /news lastmod so crawlers see the daily change ---
    sm_path = ROOT / "sitemap.xml"
    sm = sm_path.read_text(encoding="utf-8")
    new_sm = re.sub(
        r"(<loc>https://foldradar\.com/news</loc>\s*<lastmod>)[0-9-]+(</lastmod>)",
        rf"\g<1>{today.isoformat()}\g<2>", sm,
    )
    if new_sm != sm:
        sm_path.write_text(new_sm, encoding="utf-8", newline="\n")
        print("sitemap.xml /news lastmod bumped")


if __name__ == "__main__":
    build()
