#!/usr/bin/env python3
"""recipe-publish.py — scrub, render, and publish the recipe library.

Reads scraped recipe markdown from RECIPE_DEST, scrubs personal info, and
builds a self-contained static "Recipes" section into RECIPE_REPO/site:
  index.html   searchable section page (ingredient match-ALL + category chips)
  <slug>.html  full recipe page
  data.json    search corpus consumed by search.js
  search.js    client-side filtering

The section folder is deployed both to Caddy (recipes.guison.net -> /srv/recipes)
and to the portfolio site on Forge (guison.net/recipes/).
"""
import os
import re
import sys
import json
import html
import shutil
import datetime

SRC_DIR = os.path.expanduser(os.environ.get("RECIPE_DEST", "~/Documents/Recipes"))
REPO_DIR = os.path.expanduser(os.environ.get("RECIPE_REPO", "~/opencode-workspace/recipes"))
SITE_DIR = os.path.join(REPO_DIR, "site")
PUBLIC_RECIPES = os.path.join(REPO_DIR, "recipes")
PIPELINE_DIR = os.path.join(REPO_DIR, "pipeline")
SCRIPTS_DIR = os.path.join(REPO_DIR, "scripts")

REDACT = "[redacted]"

SCRUB_RULES = [
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[A-Za-z]{2,}\b"), REDACT),
    (re.compile(r"/home/[a-z0-9][a-z0-9_.-]*"), REDACT),
    (re.compile(r"~?/\.thunderbird[a-zA-Z0-9/_ .-]*"), REDACT),
    (re.compile(r"[a-f0-9]{6,12}\.[A-Za-z0-9_-]+(?:-[A-Za-z0-9_-]+)?"), REDACT),
    (re.compile(r"(?:Documents|\.config|\.cache)/recipe-agent"), REDACT),
    (re.compile(r"\b(?:recipe|processed)\.log\b"), REDACT),
    (re.compile(r"\bJordan\b"), REDACT),
    (re.compile(r"\b(?:1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"), REDACT),
    (re.compile(r"\b(?:Message-ID|message-id):[^\n]+", re.I), ""),
]

SUSPECT_PATTERNS = [
    re.compile(r"\b[\w.+-]+@[\w-]+\.[A-Za-z]{2,}\b"),
    re.compile(r"/home/[a-z0-9]"),
    re.compile(r"thunderbird", re.I),
    re.compile(r"\bJordan\b"),
    re.compile(r"\b(?:1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"),
    re.compile(r"\b[A-Za-z0-9]{1,12}\.(?:default(?:-release)?|release)\b"),
]

CATEGORIES = ["Appetizer", "Snack", "Drink", "Chicken", "Beef", "Steak", "Dessert", "Medicine"]

CAT_KEYWORDS = [
    ("Medicine", r"\b(medicine|remedy|herbal|tincture|decoction|elixir|healing|cough|"
                 r"cold remedy|immune|detox|cleansing|holistic|supplement|infusion|tonic)\b"),
    ("Steak", r"\b(steak|ribeye|sirloin|t-bone|strip steak|ny strip)\b"),
    ("Beef", r"\b(beef|ground beef|mince|brisket|roast beef|corned beef|meatball|beef stew)\b"),
    ("Chicken", r"\b(chicken|drumstick|wings?|poultry|hens?)\b"),
    ("Drink", r"\b(drink|smoothie|juice|shake|cocktail|mocktail|coffee|latte|tea|"
              r"lemonade|kombucha|iced|broth|soup)\b"),
    ("Dessert", r"\b(dessert|cake|cookie|brownie|muffin|pie|pudding|custard|cheesecake|"
                 r"ice cream|candy|sorbet|sweet|chocolate|trifle|bar)\b"),
    ("Appetizer", r"\b(appetizer|starter|dip|bruschetta|spring roll|nacho|guacamole|salsa|"
                  r"finger food|deviled|crostini|hummus|meze|tapas)\b"),
    ("Snack", r"\b(snack|trail mix|granola|popcorn|protein ball|energy ball|bites?|jerky)\b"),
]

_SCRUB_PASSED = 0
_SCRUB_FAILED = 0


def scrub(text):
    global _SCRUB_PASSED, _SCRUB_FAILED
    for pat, rep in SCRUB_RULES:
        text = pat.sub(rep, text)
    for pat in SUSPECT_PATTERNS:
        if pat.search(text):
            _SCRUB_FAILED += 1
            return None
    _SCRUB_PASSED += 1
    return text


def esc(s):
    return html.escape(s, quote=True)


def parse_frontmatter(md):
    meta = {}
    if md.startswith("---"):
        end = md.find("\n---", 3)
        if end != -1:
            for line in md[3:end].splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    k = k.strip()
                    v = v.strip().strip('"')
                    if "," in v:
                        v = [p.strip() for p in v.split(",")]
                    meta[k] = v
    return meta


def split_ingredients(md):
    match = re.search(r"^## Ingredients\n(.*?)(?=^## |\Z)", md, re.M | re.S)
    if not match:
        return []
    items = []
    for ln in match.group(1).splitlines():
        s = re.sub(r"^- \[[ x]?\]?\s*", "", ln.strip(), flags=re.I)
        if re.match(r"^[\u2022*-]\s+", s):
            s = re.sub(r"^[-*\u2022]\s+", "", s)
        if s and not re.match(r"^(see source|\(see source\))$", s, re.I):
            items.append(re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", s).strip())
    return items


def guess_category(*texts):
    low = " ".join(t.lower() for t in texts if t)
    counts = [(cat, len(re.findall(pat, low))) for cat, pat in CAT_KEYWORDS]
    counts.sort(key=lambda x: -x[1])
    return [cat for cat, n in counts[:2] if n > 0]


def resolve_categories(meta, md):
    given = meta.get("category", meta.get("tags", []))
    if isinstance(given, str):
        given = [given]
    known = {c.lower(): c for c in CATEGORIES}
    cats = [known.get(str(c).strip().lower()) for c in given]
    cats = [c for c in cats if c]
    if not cats:
        cats = guess_category(meta.get("title", ""), md)
    return (cats or ["Snack"])[:2]


def render_body(md):
    md = re.sub(r"^---\n.*?\n---\n*", "", md, count=1, flags=re.S)
    parts = []
    in_code = False
    active = None

    def close_list():
        nonlocal active
        if active:
            parts.append(f"</{active}>")
            active = None

    def open_list(kind):
        nonlocal active
        if active != kind:
            close_list()
            active = kind
            parts.append(f"<{kind}>")

    for ln in md.splitlines():
        if ln.startswith("```"):
            close_list()
            parts.append("<pre>" if not in_code else "</pre>")
            in_code = not in_code
            continue
        if ln.startswith("## "):
            close_list()
            parts.append(f"<h2>{esc(ln[3:])}</h2>")
            continue
        if in_code:
            parts.append(esc(ln))
            continue
        if re.match(r"^- \[ \]", ln):
            open_list("ul")
            parts.append(f"<li>{esc(ln[6:].strip())}</li>")
        elif re.match(r"^\d{1,3}\.\s", ln):
            open_list("ol")
            step = re.sub(r"^\d{1,3}\.\s*", "", ln)
            parts.append(f"<li>{esc(step)}</li>")
        elif re.match(r"^- ", ln):
            open_list("ul")
            parts.append(f"<li>{esc(ln[2:].strip())}</li>")
        elif ln.strip().startswith("http"):
            close_list()
            u = ln.strip()
            parts.append(f"<p><a href='{esc(u)}' rel='nofollow'>{esc(u)}</a></p>")
        elif ln.strip():
            close_list()
            parts.append(f"<p>{esc(ln.strip())}</p>")
    close_list()
    return "".join(parts)


def slugify(text):
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "recipe"


def badges(cats):
    return "".join(f"<span class='tag'>{esc(c)}</span>" for c in cats)


PAGE_TMPL = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title} — Recipes</title>
<style>
  :root {{ --bg:#fefefe; --text:#1a1d24; --dim:#5a6270; --accent:#0030f0; --border:#d9dce1; --sans:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif; }}
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ background:var(--bg); color:var(--text); font-family:var(--sans); line-height:1.65; padding:0 24px; }}
  .wrap {{ max-width:760px; margin:0 auto; padding:48px 0 96px; }}
  a {{ color:var(--accent); text-decoration:none; }}
  h1 {{ font-size:1.9rem; margin-bottom:4px; }}
  h2 {{ font-size:1.2rem; margin:32px 0 12px; border-bottom:1px solid var(--border); padding-bottom:6px; }}
  ul, ol {{ margin:6px 0 12px; padding-left:24px; }}
  li {{ margin:6px 0; }}
  .tag {{ display:inline-block; background:#eef1f6; border:1px solid var(--border); border-radius:999px; padding:2px 10px; font-size:.78rem; color:var(--dim); margin:2px 6px 2px 0; }}
  .tags {{ margin:4px 0 10px; }}
  .meta {{ color:var(--dim); font-size:.95rem; margin:10px 0 22px; }}
  .meta span {{ margin-right:18px; white-space:nowrap; }}
  p {{ margin:8px 0; }}
  .back {{ display:inline-block; margin-bottom:24px; font-size:.9rem; }}
</style>
</head>
<body>
<div class="wrap">
<a class="back" href="./index.html">&larr; All recipes</a>
<h1>{title}</h1>
<div class="tags">{tags}</div>
<div class="meta">
  <span>Added {date}</span>
  <span>Prep {prep}</span>
  <span>Cook {cook}</span>
  <span>Serves {servings}</span>
</div>
<p><a href="{source}" rel="nofollow">Source &rarr;</a></p>
{body}
</div>
</body>
</html>"""

SECTION_CSS = """
  :root {{ --bg:#fefefe; --text:#1a1d24; --dim:#5a6270; --accent:#0030f0; --border:#d9dce1; --sans:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif; }}
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ background:var(--bg); color:var(--text); font-family:var(--sans); line-height:1.65; padding:0 24px; }}
  .wrap {{ max-width:760px; margin:0 auto; padding:48px 0 96px; }}
  a {{ color:var(--accent); text-decoration:none; }}
  .back {{ display:inline-block; margin-bottom:24px; font-size:.9rem; color:var(--dim); }}
  h1 {{ font-size:1.9rem; margin-bottom:6px; }}
  .sub {{ color:var(--dim); font-size:.95rem; margin-bottom:28px; }}
  input[name=ing] {{ width:100%; max-width:560px; font:inherit; font-size:1rem; padding:12px 16px; border:1px solid var(--border); border-radius:8px; outline:none; }}
  input[name=ing]:focus {{ border-color:var(--accent); }}
  .chips {{ margin:16px 0 8px; }}
  .chip {{ cursor:pointer; user-select:none; display:inline-block; border:1px solid var(--border); border-radius:999px; padding:4px 12px; font-size:.82rem; color:var(--dim); margin:3px 6px 3px 0; background:#fff; }}
  .chip.on {{ background:var(--accent); border-color:var(--accent); color:#fff; }}
  .count {{ color:var(--dim); font-size:.85rem; margin:10px 0 4px; }}
  .card {{ display:block; padding:16px 2px; border-bottom:1px solid var(--border); }}
  .card .t {{ font-size:1.05rem; font-weight:600; }}
  .card .d {{ color:var(--dim); font-size:.85rem; margin-top:2px; }}
  .tag {{ display:inline-block; background:#eef1f6; border:1px solid var(--border); border-radius:999px; padding:1px 8px; font-size:.72rem; color:var(--dim); margin:3px 5px 0 0; }}
  #empty {{ display:none; color:var(--dim); margin-top:20px; }}
  noscript .card {{ display:block !important; }}
"""

SECTION_TMPL = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Recipes — guison.net</title>
<style>{css}</style>
</head>
<body>
<div class="wrap">
<a class="back" href="/">&larr; guison.net</a>
<h1>Recipes</h1>
<p class="sub">{count} recipe(s). Search by what you have, or filter by category.</p>
<input type="text" name="ing" placeholder="Ingredients you have — e.g. chicken, onion, garlic" autocomplete="off">
<div class="chips" id="chips">{chips}</div>
<p class="count" id="count"></p>
<div id="list">{cards}</div>
<p id="empty">No recipes match the current filter.</p>
</div>
<script src="./search.js"></script>
</body>
</html>"""

SEARCH_JS = """/* Recipe section filtering: ingredient match-ALL + category chips. */
(function () {
  var input = document.querySelector('input[name="ing"]');
  var chipsHost = document.getElementById('chips');
  var list = document.getElementById('list');
  var count = document.getElementById('count');
  var empty = document.getElementById('empty');
  var cards = Array.prototype.slice.call(list.querySelectorAll('.card'));
  var activeCats = {};

  chipsHost.addEventListener('click', function (e) {
    var c = e.target.closest('.chip');
    if (!c) return;
    var k = c.dataset.cat;
    if (activeCats[k]) delete activeCats[k]; else activeCats[k] = true;
    c.classList.toggle('on', !!activeCats[k]);
    apply();
  });

  function tokens(s) {
    return (s || '').toLowerCase().split(/[\s,]+/).map(function (t) {
      return t.trim().replace(/\\.$/, '');
    }).filter(function (t) { return t.length >= 2; });
  }

  function apply() {
    var want = tokens(input.value);
    var catKeys = Object.keys(activeCats);
    var shown = 0;
    cards.forEach(function (card) {
      var ok = true;
      if (catKeys.length) {
        var cats = (card.dataset.cats || '').split(',');
        ok = catKeys.some(function (k) { return cats.indexOf(k) !== -1; });
      }
      if (ok && want.length) {
        var hay = (card.dataset.ing || '').toLowerCase() + ' ' +
                  (card.dataset.cats || '').toLowerCase() + ' ' +
                  (card.dataset.tags || '').toLowerCase() + ' ' +
                  card.dataset.title.toLowerCase();
        ok = want.every(function (t) { return hay.indexOf(t) !== -1; });
      }
      card.style.display = ok ? '' : 'none';
      if (ok) shown++;
    });
    count.textContent = shown + ' of ' + cards.length + ' recipe(s)';
    empty.style.display = shown ? 'none' : 'block';
  }

  if (input) input.addEventListener('input', apply);
  apply();
})();
"""


def build_repo_folders():
    for d in (SITE_DIR, PUBLIC_RECIPES, PIPELINE_DIR, SCRIPTS_DIR):
        os.makedirs(d, exist_ok=True)


def main():
    build_repo_folders()
    mds = sorted(f for f in os.listdir(SRC_DIR) if f.endswith(".md"))
    entries = []
    corpus = []
    for fname in mds:
        with open(os.path.join(SRC_DIR, fname), encoding="utf-8") as f:
            raw = f.read()
        scrubbed = scrub(raw)
        if scrubbed is None:
            print(f"  [skip] {fname}: possible personal info left after scrub")
            continue
        slug = os.path.splitext(fname)[0]
        with open(os.path.join(PUBLIC_RECIPES, fname), "w", encoding="utf-8") as f:
            f.write(scrubbed)
        meta = parse_frontmatter(scrubbed)
        title = str(meta.get("title", slug))
        cats = resolve_categories(meta, scrubbed)
        ingredients = split_ingredients(scrubbed)
        tags = meta.get("tags", [])
        if isinstance(tags, str):
            tags = [tags]
        corpus.append({
            "slug": slug,
            "title": title,
            "date": str(meta.get("date_added", "")),
            "categories": cats,
            "tags": [str(t) for t in tags],
            "ingredients": ingredients,
            "source": str(meta.get("source_url", "")),
        })
        body = render_body(scrubbed)
        page = PAGE_TMPL.format(
            title=esc(title),
            tags=badges(cats),
            date=esc(str(meta.get("date_added", "Unknown"))),
            prep=esc(str(meta.get("prep_time", "?"))),
            cook=esc(str(meta.get("cook_time", "?"))),
            servings=esc(str(meta.get("servings", "?"))),
            source=esc(str(meta.get("source_url", "#"))),
            body=body,
        )
        with open(os.path.join(SITE_DIR, f"{slug}.html"), "w", encoding="utf-8") as f:
            f.write(page)
        entries.append((title, str(meta.get("date_added", "")), f"{slug}.html", cats, ingredients, [str(t) for t in tags]))

    entries.sort(key=lambda e: e[1], reverse=True)
    chips = "".join(f"<span class='chip' data-cat='{c}'>{c}</span>" for c in CATEGORIES)
    cards = "".join(
        "<a class='card' href='./{href}' data-title='{title}' data-cats='{cat}' "
        "data-ing='{ing}' data-tags='{tags}'><span class='t'>{title}</span>"
        "<div class='d'>{date} &mdash; {n} ingredient(s)</div>{badges}</a>".format(
            href=esc(href), title=esc(t), cat=esc(",".join(cat)),
            ing=esc(" ".join(ing)), tags=esc(",".join(tags)),
            date=esc(d), n=len(ing), badges=badges(cat))
        for t, d, href, cat, ing, tags in entries)
    with open(os.path.join(SITE_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(SECTION_TMPL.format(css=SECTION_CSS, count=len(entries),
                                    chips=chips, cards=cards))
    with open(os.path.join(SITE_DIR, "search.js"), "w", encoding="utf-8") as f:
        f.write(SEARCH_JS)
    with open(os.path.join(SITE_DIR, "data.json"), "w", encoding="utf-8") as f:
        json.dump(corpus, f, ensure_ascii=False, indent=2)

    bin_src = os.path.expanduser(os.environ.get("RECIPE_BIN_DIR", "~/bin"))
    for src, dst in ((os.path.join(bin_src, "recipe-ingest.py"), "recipe-ingest.py"),
                     (os.path.join(bin_src, "recipe-publish.py"), "recipe-publish.py")):
        shutil.copy(src, os.path.join(PIPELINE_DIR, dst))
    print(f"Published {len(entries)} recipe(s) to {REPO_DIR}")
    print(f"Scrub: {_SCRUB_PASSED} passed, {_SCRUB_FAILED} blocked")
    if _SCRUB_FAILED:
        print("Some recipe(s) were skipped due to possible personal info.", file=sys.stderr)
        sys.exit(2 if not entries else 0)
    return 0


if __name__ == "__main__":
    sys.exit(main())