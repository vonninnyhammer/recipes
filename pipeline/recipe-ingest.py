#!/usr/bin/env python3
import os
import re
import sys
import html
import json
import time
import shutil
import mailbox
import datetime
import subprocess
import urllib.request
from html.parser import HTMLParser

PROFILE = os.environ.get("RECIPE_THUNDERBIRD_PROFILE", "")
DEST_DIR = os.path.expanduser(os.environ.get("RECIPE_DEST", "~/Documents/Recipes"))
LEDGER = os.path.expanduser(os.environ.get("RECIPE_LEDGER", "~/.config/recipe-agent/processed.log"))
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
JINA = "https://r.jina.ai/"
YTDLP = os.path.expanduser(os.environ.get("RECIPE_YTDLP", "~/.local/bin/yt-dlp"))
CACHE = os.path.expanduser(os.environ.get("RECIPE_CACHE", "~/.cache/recipe-agent"))
LOCAL_ROOT = None
IMAP_ROOT = None


def discover_profile():
    explicit = os.environ.get("RECIPE_THUNDERBIRD_PROFILE", "")
    if explicit and os.path.isdir(explicit):
        return explicit
    tb = os.path.expanduser("~/.thunderbird")
    if not os.path.isdir(tb):
        return None
    for name in sorted(os.listdir(tb)):
        prof = os.path.join(tb, name)
        if not os.path.isdir(prof):
            continue
        imap = os.path.join(prof, "ImapMail")
        if os.path.isdir(imap):
            for acc in os.listdir(imap):
                accdir = os.path.join(imap, acc)
                if not os.path.isdir(accdir):
                    continue
                if any(n.lower().startswith("recipes") for n in os.listdir(accdir)):
                    return prof
        local = os.path.join(prof, "Mail", "Local Folders")
        if os.path.isdir(local) and any(n.lower().startswith("recipes") for n in os.listdir(local)):
            return prof
    return None

JUNK_HOSTS = re.compile(
    r"(unsubscribe|confirmation|doubleclick|google|gstatic|yahoo|microsoft|aol|"
    r"apple|facebook\.com/plugins|twitter\.com/(intent|share)|fb\.me|tracking|"
    r"analytics|click\.(s?endgrid|market)|s3\.amazonaws|amazonaws|wix|typeform|"
    r"mailchimp|sendinblue|brevo|klaviyo|open\.spotify|pixel|cdn|cloudfront)",
    re.I,
)
JUNK_SUBSTR = re.compile(r"(unsubscribe|preferences|/u/\d|utm_|spm=|tracking|click\?)", re.I)

FOLLOW_SOCIAL = re.compile(
    r"(facebook\.com|\.fbcdn|tiktokcdn|scontent\.|cdninstagram|youtube\.com|youtu\.be|"
    r"l\.facebook\.com|fb\.watch|instagram\.com/|reddit\.com|pinterest|linkedin|"
    r"twitter\.com|soundcloud|spotify)", re.I)
FOLLOW_PREFER = re.compile(
    r"(recipe|food|kitchen|eats|cook|bake|substack\.com|medium\.com|notion\.site|"
    r"allrecipes|delish|seriouseats|foodnetwork|tasty|bbcgoodfood|bonappetit|marthastewart|"
    r"thekitchn|kingarthur|tasteofhome|natashaskitchen|spoonforkbacon)", re.I)
SKIP_OCR_HOSTS = re.compile(r"(rsrc\.php|facebook\.com/images|/icons/|fbLogo|widget|logo\.|sprite)", re.I)

FOOD_HINTS = re.compile(
    r"\b(cup|cups|tbsp|tbs|tsp|teaspoon|tablespoon|gram|grams|ounce|ounces|kg|lb|lbs|ml|"
    r"g\b|flour|sugar|salt|butter|oil|egg|eggs|garlic|onion|ingredients?|recipe|bake|baked|"
    r"cook|mix|stir|heat|oven|simmer|whisk|blend|dough|sauce|marinate|chop|boil|fry|serve|"
    r"preheat|minutes?|prep time|cook time)\b",
    re.I,
)

CATEGORIES = ["Appetizer", "Snack", "Drink", "Chicken", "Beef", "Steak", "Dessert", "Medicine"]

CAT_KEYWORDS = [
    ("Medicine", r"\b(medicine|remedy|herbal|tincture|decoction|elixir|healing|cough|"
                 r"cold remedy|immune|detox|cleansing|holistic|supplement|infusion|tonic)\b"),
    ("Steak", r"\b(steak|ribeye|filet sirloin|sirloin|t-bone|strip steak|ny strip)\b"),
    ("Beef", r"\b(beef|ground beef|mince|brisket|roast beef|corned beef|meatball|beef stew)\b"),
    ("Chicken", r"\b(chicken|drumstick|wings?|poultry|hens?)\b"),
    ("Drink", r"\b(drink|smoothie|juice|shake|cocktail|mocktail|coffee|latte|tea|"
              r"lemonade|kombucha|iced|broth|soup)\b"),
    ("Dessert", r"\b(dessert|cake|cookie|brownie|muffin|pie|pudding|custard|cheesecake|"
                 r"ice cream|candy|sorbet|sweet|chocolate|trifle|doughnut|donut|bar)\b"),
    ("Appetizer", r"\b(appetizer|starter|dip|bruschetta|spring roll|nacho|guacamole|salsa|"
                  r"finger food|deviled|crostini|hummus|meze|tapas)\b"),
    ("Snack", r"\b(snack|trail mix|granola|popcorn|protein ball|energy ball|bites?|jerky)\b"),
]


def guess_category(*texts):
    low = " ".join(t.lower() for t in texts if t)
    counts = [(cat, len(re.findall(pat, low))) for cat, pat in CAT_KEYWORDS]
    counts.sort(key=lambda x: -x[1])
    return [cat for cat, n in counts[:2] if n > 0]


def apply_category(markdown, *texts):
    m = re.search(r"^category:\s*\"([^\"]*)\"", markdown, re.M)
    given = [c.strip() for c in (m.group(1).split(",") if m and m.group(1) else [])]
    known = {c.lower(): c for c in CATEGORIES}
    cats = [known.get(c.lower()) for c in given if known.get(c.lower())]
    cats = [c for c in cats if c]
    if not cats:
        cats = guess_category(*texts)
    cats = cats[:2] or ["Snack"]
    line = 'category: "' + ", ".join(cats) + '"'
    return re.sub(r"^category:\s*\".*?\"", line, markdown, count=1, flags=re.M)


def cleanup_artifacts():
    for fn in os.listdir(CACHE):
        if fn.startswith("subtitle") or fn.startswith("img_"):
            try:
                os.remove(os.path.join(CACHE, fn))
            except OSError:
                pass


class LinkExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.text = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            for k, v in attrs:
                if k.lower() in ("href",):
                    self.links.append(v)

    def handle_data(self, data):
        self.text.append(data)

    @property
    def text_content(self):
        return html.unescape(" ".join("".join(self.text).split()))


def candidate_stores():
    roots = []
    if LOCAL_ROOT and os.path.isdir(LOCAL_ROOT):
        roots.append(LOCAL_ROOT)
    if IMAP_ROOT and os.path.isdir(IMAP_ROOT):
        for acc in os.listdir(IMAP_ROOT):
            accdir = os.path.join(IMAP_ROOT, acc)
            if os.path.isdir(accdir):
                roots.append(accdir)
    found = []
    for root in roots:
        try:
            entries = os.listdir(root)
        except OSError:
            continue
        for name in entries:
            if not name.lower().startswith("recipe"):
                continue
            path = os.path.join(root, name)
            if os.path.isdir(path):
                for sub in ("new", "cur", "tmp"):
                    if os.path.isdir(os.path.join(path, sub)):
                        found.append(("maildir", os.path.join(path, sub)))
            elif os.path.isfile(path):
                if path.endswith(".msf"):
                    found.append(("empty", path))
                else:
                    found.append(("mbox", path))
    return found


def extract_urls(payload, is_html):
    urls = []
    if is_html:
        parser = LinkExtractor()
        try:
            parser.feed(payload)
            urls.extend(parser.links)
        except Exception:
            pass
    for m in re.findall(r"https?://[^\s<>\"'\)\]]+", payload):
        url = m.rstrip(".,;:!?")
        urls.append(url)
    seen = set()
    clean = []
    for u in urls:
        u = (u or "").strip().split("#")[0]
        if not u or u in seen:
            continue
        if JUNK_HOSTS.search(u) or JUNK_SUBSTR.search(u):
            continue
        if re.search(r"\.(png|jpe?g|gif|webp|svg|js|css)(\?|$)", u, re.I):
            continue
        clean.append(u)
        seen.add(u)
    return clean


def recipe_candidates(urls):
    social = re.compile(
        r"(instagram\.com|tiktok\.com|facebook\.com/(share|.*/posts|.*/videos|story|reel)"
        r"|youtube\.com/(watch|short)|youtu\.be|pin\.it|pinterest|reddit\.com|twitter\.com)", re.I)
    return sorted(urls, key=lambda u: not bool(social.search(u)))


def platform_of(url):
    if re.search(r"youtube\.com/(shorts|watch)|youtu\.be/", url):
        return "youtube"
    if re.search(r"tiktok\.com/", url):
        return "tiktok"
    if re.search(r"instagram\.com/", url):
        return "instagram"
    if re.search(r"(facebook\.com/|fb\.watch/)", url):
        return "facebook"
    return "web"


def jina_title(text):
    for ln in (text or "").splitlines():
        if ln.lower().startswith("title:"):
            return ln.split(":", 1)[1].strip()
        if ln.startswith("URL Source:"):
            break
    return None


def clean_page_title(text):
    t = jina_title(text) or ""
    t = re.sub(r"^[\d.,]+[kKmMbB]?\s*(views|reactions|likes|followers)?.*?\|\s*", "", t)
    part = re.split(r"\s*\|\s*", t, maxsplit=1)[0].strip()
    part = re.sub(r"\s*(follow|visit|via|watch)\b.*$", "", part, flags=re.I)
    part = re.sub(r"\s*#(shorts|recipe|fyp)$", "", part, flags=re.I)
    return part or None


def iter_messages(store_type, path):
    if store_type == "maildir":
        newdir = os.path.join(path, "..", "new")
        for fname in sorted(os.listdir(newdir)):
            fpath = os.path.join(newdir, fname)
            if os.path.isfile(fpath):
                yield fpath, fname, None
    else:
        try:
            mb = mailbox.mbox(path, create=False)
        except Exception as e:
            print(f"  [skip] cannot open mbox {path}: {e}", file=sys.stderr)
            return
        for key, msg in mb.items():
            yield None, None, msg


def plain_body(msg):
    parts = []
    alt_plain = None
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain":
                try:
                    payload = part.get_payload(decode=True)
                    charset = part.get_content_charset() or "utf-8"
                    text = payload.decode(charset, errors="replace")
                    alt_plain = text if alt_plain is None else alt_plain
                except Exception:
                    continue
            elif ct == "text/html":
                try:
                    payload = part.get_payload(decode=True)
                    charset = part.get_content_charset() or "utf-8"
                    html_text = payload.decode(charset, errors="replace")
                    parts.append(html_text)
                except Exception:
                    continue
    else:
        ct = msg.get_content_type()
        try:
            payload = msg.get_payload(decode=True)
            charset = msg.get_content_charset() or "utf-8"
            text = payload.decode(charset, errors="replace")
            if ct == "text/html":
                parts.append(text)
            else:
                alt_plain = text
        except Exception:
            pass
    return alt_plain or "", parts


def fetch(recipe_url):
    for attempt in range(1, 4):
        try:
            req = urllib.request.Request(JINA + recipe_url, headers={"User-Agent": "Mozilla/5.0 (recipe-agent)"})
            with urllib.request.urlopen(req, timeout=90) as resp:
                text = resp.read().decode("utf-8", errors="replace")
            if "URL Source:" not in text and len(text) < 200:
                raise RuntimeError("empty/unusual response")
            return text
        except Exception as e:
            print(f"  [warn] fetch {recipe_url} attempt {attempt}/3 failed: {e}", file=sys.stderr)
            if attempt < 3:
                time.sleep(8 * attempt)
    return None


def markdown_images(text):
    return re.findall(r"!\[[^\]]*\]\(([^)]+)\)", text or "")


def food_score(text):
    if not text:
        return 0
    toks = re.findall(r"[A-Za-z]+", text.lower())
    matches = [t for t in toks if FOOD_HINTS.match(t)]
    return len(matches)


def find_recipe_links(text, exclude):
    urls = re.findall(r"https?://[^\s()<>\[\]\"']+", text or "")
    for m in re.finditer(r"!\[[^\]]*\]\(([^)]+)\)|\[[^\]]*\]\(([^)]+)\)", text or ""):
        for g in m.groups():
            if g:
                urls.append(g)
    cands = []
    for u in urls:
        u = (u or "").strip().rstrip(".,;:!?")
        u = u.split("#")[0]
        if not u or "r.jina.ai" in u or u == exclude:
            continue
        u_path = u.split("?", 1)[0]
        if JUNK_HOSTS.search(u_path) or JUNK_SUBSTR.search(u_path) or FOLLOW_SOCIAL.search(u):
            continue
        if re.search(r"\.(png|jpe?g|gif|webp|svg)(\?|$)", u, re.I):
            continue
        cands.append(u)
    return sorted(set(cands), key=lambda u: not bool(FOLLOW_PREFER.search(u)))[:2]


def fetch_youtube_captions(url):
    if not os.path.exists(YTDLP):
        return None
    os.makedirs(CACHE, exist_ok=True)
    try:
        cmd = [
            YTDLP, "--skip-download", "--no-warnings", "--quiet",
            "--write-auto-subs", "--write-subs",
            "--sub-langs", "en.*", "--sub-format", "srt/vtt",
            "--paths", CACHE, "--no-overwrites", "-o", "subtitle", url,
        ]
        subprocess.run(cmd, timeout=120, capture_output=True)
        for f in sorted(os.listdir(CACHE)):
            if f.startswith("subtitle") and re.search(r"\.(srt|vtt)$", f):
                fpath = os.path.join(CACHE, f)
                text = clean_subtitle(open(fpath, encoding="utf-8", errors="replace").read())
                os.remove(fpath)
                return text
    except Exception as e:
        print(f"  [warn] caption fetch failed: {e}", file=sys.stderr)
    return None


def clean_subtitle(raw):
    lines = []
    for ln in raw.splitlines():
        if re.match(r"^\d{1,2}:", ln) or re.match(r"^\d{2,}:\d{2}", ln):
            continue
        if "-->" in ln or ln.strip().isdigit():
            continue
        ln = re.sub(r"<[^>]+>", "", ln).strip()
        if ln:
            lines.append(ln)
    return " ".join(lines)


def ocr_images(image_urls):
    try:
        import easyocr
        reader = easyocr.Reader(["en"], gpu=False, verbose=False)
    except Exception as e:
        print(f"  [warn] easyocr unavailable: {e}", file=sys.stderr)
        return None
    os.makedirs(CACHE, exist_ok=True)
    texts = []
    seen = set()
    for url in image_urls:
        if url in seen or SKIP_OCR_HOSTS.search(url):
            continue
        seen.add(url)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (recipe-agent)"})
            data = urllib.request.urlopen(req, timeout=60).read()
            if len(data) < 6000 or len(data) > 15_000_000:
                continue
            import io
            from PIL import Image
            w, h = Image.open(io.BytesIO(data)).size
            if w < 260 or h < 260:
                continue
            fpath = os.path.join(CACHE, f"img_{len(texts):03d}.jpg")
            with open(fpath, "wb") as f:
                f.write(data)
            res = reader.readtext(fpath, detail=0)
            texts.extend(res)
        except Exception as e:
            print(f"  [warn] ocr image failed {url}: {e}", file=sys.stderr)
        if len(texts) > 250:
            break
    return " ".join(texts)


def ollama_chat_model():
    try:
        with urllib.request.urlopen(OLLAMA_URL + "/api/tags", timeout=5) as r:
            data = json.load(r)
        names = [m["name"] for m in data.get("models", [])]
        preferred = ["qwen2.5:3b", "llama3.2:3b", "gemma2:2b", "qwen2.5:1.5b", "phi3:mini"]
        for p in preferred:
            if any(p in n for n in names):
                return p
        for model in data.get("models", []):
            if "chat" in (model.get("capabilities") or []):
                return model["name"]
        for model in data.get("models", []):
            if "embed" not in model["name"].lower():
                return model["name"]
    except Exception:
        pass
    return None


def format_with_llm(model, title, url, content, note=""):
    prompt = (
        "Extract the recipe from the text below (it may be a webpage, a social caption, "
        "or a video transcript). If the text contains several recipes, return ONLY the one "
        "matching the title given. Output ONLY a Markdown file using this exact template, "
        "every field present; use Unknown where a value is absent; prep_time/cook_time in "
        "minutes; tags as a short list; category must be 1-2 values chosen ONLY from "
        "Appetizer, Snack, Drink, Chicken, Beef, Steak, Dessert, Medicine (comma separated):\n\n"
        "---\n"
        f'title: "{title}"\n'
        f'source_url: "{url}"\n'
        f'date_added: "{datetime.date.today().isoformat()}"\n'
        'prep_time: "Unknown"\n'
        'cook_time: "Unknown"\n'
        'servings: "Unknown"\n'
        'category: ""\n'
        "tags: []\n"
        "---\n\n"
        "## Ingredients\n- [ ] item\n\n## Instructions\n1. step\n"
    )
    if note:
        prompt += "\nNote: " + note + "\n"
    prompt += "\nContent:\n" + content[:15000]
    try:
        body = json.dumps({
            "model": model, "prompt": prompt, "stream": False,
            "keep_alive": "30m",
            "think": False,
            "options": {"temperature": 0.1, "num_predict": 1200},
        }).encode()
        req = urllib.request.Request(OLLAMA_URL + "/api/generate", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=300) as r:
            resp = json.load(r)
        out = resp.get("response") or ""
        out = re.sub(r"^.*?```(markdown)?\n?", "", out, flags=re.S) if "```" in out else out
        out = re.sub(r"```\s*$", "", out)
        if cross_validation(out):
            return out
        print("  [warn] llm output malformed; using structural parse", file=sys.stderr)
    except Exception as e:
        print(f"  [warn] ollama failed ({e}); using structural parse", file=sys.stderr)
    return None


def cross_validation(md):
    return "## Ingredients" in md and "## Instructions" in md and "source_url:" in md


def structural_format(title, url, content):
    meta, ingredients, instructions = parse_recipe_card(content)
    title_candidate = title or meta.get("title") or detect_title(content.splitlines())
    ing_lines = "\n".join(f"- [ ] {i}" for i in ingredients[:25]) if ingredients else "- [ ] (see source)"
    instr_lines = "\n".join(f"{n}. {s}" for n, s in enumerate(instructions[:25], 1)) if instructions else "1. (see source)"
    return (
        "---\n"
        f'title: "{title_candidate}"\n'
        f'source_url: "{url}"\n'
        f'date_added: "{datetime.date.today().isoformat()}"\n'
        f'prep_time: "{meta.get("prep", "Unknown")}"\n'
        f'cook_time: "{meta.get("cook", "Unknown")}"\n'
        f'servings: "{meta.get("servings", "Unknown")}"\n'
        'category: ""\n'
        "tags: []\n"
        "---\n\n"
        "## Ingredients\n" + ing_lines + "\n\n## Instructions\n" + instr_lines + "\n"
    )


def parse_recipe_card(content):
    meta = {}
    bullets = []
    ingredients = []
    instructions = []
    step_mode = False
    for ln in (content or "").splitlines():
        s = ln.strip()
        low = s.lower()
        if re.match(r"^(prep(?:aration)? time|prep time|cooking time|cook time|total time|servings?):", low):
            key = None
            if "prep" in low:
                key = "prep"
            elif "cook" in low or "cooking" in low:
                key = "cook"
            if key:
                val = re.sub(r"^[^:]*:\s*", "", s).strip()
                if val.lower() not in ("unknown", ""):
                    meta[key] = val
            elif "serving" in low:
                val = re.sub(r"^[^:]*:\s*", "", s).strip()
                if val:
                    meta["servings"] = val
            continue
        if not s or len(s) < 3:
            continue
        if re.match(r"^[\u2022*]\s+", s) or re.match(r"^-+\s+", s):
            item = re.sub(r"^[-*\u2022]\s+", "", s).strip()
            if item and not re.match(r"^(notes?|ingredients?|method|directions?)$", item, re.I):
                bullets.append(item)
            continue
        if re.match(r"^\d{1,3}[.)]\s+", s):
            step = re.sub(r"^\d{1,3}[.)]\s+", "", s).strip()
            commit_ingredients(bullets, ingredients)
            bullets = []
            step_mode = True
            if step:
                instructions.append(step)
            continue
        if step_mode and _looks_prose(s):
            instructions[-1] = instructions[-1] + " " + s
        commit_ingredients(bullets, ingredients)
        bullets = []
    commit_ingredients(bullets, ingredients)
    return meta, ingredients, instructions


def commit_ingredients(bullets, ingredients):
    if len(bullets) >= 2:
        ingredients.extend(bullets)


def _looks_prose(s):
    return (len(s.split()) >= 6
            and not s.startswith("#")
            and not re.match(r"^\d", s)
            and not re.match(r"^[-*\u2022]", s)
            and not re.match(r"^(image|img) \d+", s, re.I))


def detect_title(lines):
    for ln in lines[:40]:
        s = ln.strip()
        if re.match(r"^#\s+\S", s):
            return re.sub(r"^#+\s*", "", s)
        if len(s) < 90 and s and not re.match(r"^(https?://|\||-|\*)", s):
            return s
    return "Untitled Recipe"


def slugify(text):
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "recipe"


def already_processed(tag):
    tag = tag.strip()
    if not tag:
        return False
    if os.path.exists(LEDGER):
        try:
            with open(LEDGER) as f:
                return tag in f.read()
        except OSError:
            return False
    return False


def mark_processed(tag):
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    with open(LEDGER, "a") as f:
        f.write(tag + "\n")


def main():
    global LOCAL_ROOT, IMAP_ROOT
    prof = discover_profile()
    if not prof:
        print("No Thunderbird profile with a 'Recipes' folder found.")
        print("Set RECIPE_THUNDERBIRD_PROFILE or create the folder in Thunderbird first.")
        return 3
    LOCAL_ROOT = os.path.join(prof, "Mail", "Local Folders")
    IMAP_ROOT = os.path.join(prof, "ImapMail")
    os.makedirs(DEST_DIR, exist_ok=True)
    stores = candidate_stores()
    if not stores:
        print("No 'Recipes' folder found yet (create it in Thunderbird first).")
        return 0
    print(f"Found {len(stores)} candidate recipe store(s)")
    model = ollama_chat_model()
    if model:
        print(f"Using ollama model: {model}")
    else:
        print("No chat model on ollama; structural parsing only.")
    processed = 0
    skipped = 0
    for store_type, path in stores:
        if store_type == "empty":
            print(f"  store: exists but no mail synced yet ({os.path.basename(path)})")
            continue
        print(f"  store: {store_type} -> {path}")
        for fpath, fname, msg in iter_messages(store_type, path):
            if msg is None:
                src = os.path.join(path, "..", "new", fname)
                if not os.path.isfile(src):
                    continue
                with open(src, "rb") as f:
                    msg = mailbox.mboxMessage(f)
            msgid = msg.get("Message-ID", "").strip() or (fname or "")
            if already_processed(msgid):
                skipped += 1
                continue
            plain, html_parts = plain_body(msg)
            urls = extract_urls(plain, False)
            for raw in html_parts:
                urls = extract_urls(raw, True)
            url = recipe_candidates(urls)
            if not url:
                mark_processed(msgid)
                skipped += 1
                continue
            url = url[0]
            subj = re.sub(r"\s+", " ", msg.get("Subject", "") or "").strip()
            print(f"  message: {subj or '(no subject)'}")
            print(f"    url: {url}  [{platform_of(url)}]")
            fetched = fetch(url)
            if not fetched:
                mark_processed(msgid)
                skipped += 1
                continue
            print(f"    page title: {jina_title(fetched) or '(none)'}")
            orig_url = url
            content = fetched
            base = food_score(fetched)
            platform = platform_of(url)
            followed = None
            for link in find_recipe_links(fetched, url):
                print(f"    + following recipe link: {link}")
                extra = fetch(link)
                if extra and food_score(extra) > base:
                    content = extra
                    base = food_score(extra)
                    followed = link
                    print(f"    + full recipe found ({base} food markers)")
            need_more = platform != "web" or base < 6
            if need_more:
                if platform == "youtube":
                    cap = fetch_youtube_captions(url)
                    if cap:
                        print(f"    + transcript ({len(cap)} chars)")
                        content += "\nTRANSCRIPT: " + cap
                elif base < 6:
                    imgs = markdown_images(fetched)
                    if imgs:
                        print(f"    + OCR on {len(imgs)} image(s)")
                        ocr = ocr_images(imgs[:6])
                        if ocr:
                            print(f"    + OCR text ({len(ocr)} chars)")
                            content += "\nOCR: " + ocr
            ptitle = clean_page_title(fetched)
            if subj and not re.match(r"^(fwd|re|fw)\b", subj, re.I):
                title = subj.rsplit(":", 1)[-1].strip() if ":" in subj else subj.strip()
            elif ptitle:
                title = ptitle
            else:
                title = "Untitled Recipe"
            title = title or "Untitled Recipe"
            if model:
                out = format_with_llm(model, title, orig_url, content)
                markdown = out or structural_format(title, orig_url, content)
            else:
                markdown = structural_format(title, orig_url, content)
                if food_score(content) >= 6 and platform_of(orig_url) != "web" and "TRANSCRIPT" in content:
                    markdown += "\n## Notes\nRaw transcript:\n\n```\n" + content.split("TRANSCRIPT: ", 1)[1][:2000] + "\n```\n"
            if followed:
                markdown += f"\n## Full recipe\n{followed}\n"
            markdown = re.sub(r"\n(#{1,6}\s)", r"\n\n\1", markdown)
            markdown = apply_category(markdown, title, content)
            fname_out = slugify(title) + ".md"
            dest = os.path.join(DEST_DIR, fname_out)
            n = 2
            while os.path.exists(dest):
                dest = os.path.join(DEST_DIR, f"{slugify(title)}-{n}.md")
                n += 1
            with open(dest, "w") as f:
                f.write(markdown if markdown.endswith("\n") else markdown + "\n")
            mark_processed(msgid)
            processed += 1
            print(f"    saved: {dest}")
            cleanup_artifacts()
            if store_type == "maildir":
                cur = os.path.join(path, "..", "cur", fname)
                try:
                    shutil.move(os.path.join(path, "..", "new", fname), cur)
                except OSError:
                    pass
    print(f"\nSummary: {processed} recipe(s) processed, {skipped} skipped/already known.")
    return 0


if __name__ == "__main__":
    sys.exit(main())