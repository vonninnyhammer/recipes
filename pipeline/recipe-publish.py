#!/usr/bin/env python3
import os
import re
import sys
import html
import shutil
import datetime
import urllib.parse

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
                    meta[k] = v
    return meta


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
            name = ln[3:].strip().lower()
            section = "ol" if any(k in name for k in ("instruction", "direction", "method")) else "ul"
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
  .meta {{ color:var(--dim); font-size:.95rem; margin:10px 0 28px; }}
  .meta span {{ margin-right:18px; white-space:nowrap; }}
  h2 {{ font-size:1.2rem; margin:32px 0 12px; border-bottom:1px solid var(--border); padding-bottom:6px; }}
  ul, ol {{ margin:6px 0 12px; padding-left:24px; }}
  li {{ margin:6px 0; }}
  li.ing::marker {{ content:"☐ "; }}
  p {{ margin:8px 0; }}
  pre {{ background:#f4f5f7; padding:16px; border-radius:8px; overflow-x:auto; font-size:.85rem; }}
  .back {{ display:inline-block; margin-bottom:24px; font-size:.9rem; }}
</style>
</head>
<body>
<div class="wrap">
<a class="back" href="./index.html">&larr; All recipes</a>
<h1>{title}</h1>
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

INDEX_TMPL = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Recipes</title>
<style>
  :root {{ --bg:#fefefe; --text:#1a1d24; --dim:#5a6270; --accent:#0030f0; --border:#d9dce1; --sans:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif; }}
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ background:var(--bg); color:var(--text); font-family:var(--sans); line-height:1.65; padding:0 24px; }}
  .wrap {{ max-width:760px; margin:0 auto; padding:48px 0 96px; }}
  h1 {{ font-size:1.9rem; margin-bottom:16px; }}
  a {{ color:var(--accent); text-decoration:none; }}
  ul {{ list-style:none; padding-left:0; }}
  li {{ padding:14px 0; border-bottom:1px solid var(--border); }}
  .t {{ font-size:1.1rem; }}
  .d {{ color:var(--dim); font-size:.88rem; margin-top:2px; }}
</style>
</head>
<body>
<div class="wrap">
<h1>Recipes</h1>
<p>{count} recipe(s) — auto-picked from shared social links.</p>
<ul>{items}</ul>
</div>
</body>
</html>"""


def build_repo_folders():
    for d in (SITE_DIR, os.path.join(SITE_DIR, "recipes"), PUBLIC_RECIPES, PIPELINE_DIR, SCRIPTS_DIR):
        os.makedirs(d, exist_ok=True)


def main():
    build_repo_folders()
    mds = sorted(f for f in os.listdir(SRC_DIR) if f.endswith(".md"))
    entries = []
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
        title = meta.get("title", slug)
        body = render_body(scrubbed)
        page = PAGE_TMPL.format(
            title=esc(title),
            date=esc(meta.get("date_added", "Unknown")),
            prep=esc(meta.get("prep_time", "?")),
            cook=esc(meta.get("cook_time", "?")),
            servings=esc(meta.get("servings", "?")),
            source=esc(meta.get("source_url", "#")),
            body=body,
        )
        with open(os.path.join(SITE_DIR, "recipes", f"{slug}.html"), "w", encoding="utf-8") as f:
            f.write(page)
        entries.append((title, meta.get("date_added", ""), f"{slug}.html"))
    entries.sort(key=lambda e: e[1], reverse=True)
    items = "".join(
        f"<li><a class='t' href='recipes/{href}'>{esc(t)}</a><div class='d'>{esc(d)}</div></li>"
        for t, d, href in entries)
    with open(os.path.join(SITE_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(INDEX_TMPL.format(count=len(entries), items=items))
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