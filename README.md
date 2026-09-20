# Recipes

A small, self-hosted recipe collector. A cron job watches a local mail folder,
pulls recipe links out of social-media share emails (Instagram, TikTok,
Facebook Reels, YouTube Shorts), fetches the page/transcript/carousel text, and
renders each into a clean, structured Markdown recipe. The library is published
to this repository, to `recipes.guison.net`, and as a searchable section of the
portfolio at `guison.net/recipes`.

## How it works

1. Emails land in a local **Recipes** mail folder (moved there manually or by a
   Thunderbird filter).
2. `pipeline/recipe-ingest.py` scans the folder, extracts the shared URL,
   fetches clean text via the Jina Reader, follows in-post "full recipe" links,
   pulls YouTube auto-captions with `yt-dlp`, and OCRs carousel images with
   EasyOCR when needed.
3. Text is formatted into a standard recipe template (YAML frontmatter +
   Ingredients + Instructions) by a local Ollama model (`qwen2.5:3b`), with a
   structural parser as fallback. Each recipe is tagged with 1–2 categories from
   a fixed list: Appetizer, Snack, Drink, Chicken, Beef, Steak, Dessert,
   Medicine.
4. `pipeline/recipe-publish.py` scrubs the output for personal information,
   writes the Markdown to `recipes/`, renders the static section in `site/`, and
   builds the search corpus in `site/data.json`.
5. `scripts/daily.sh` (cron, 08:00) runs ingest → publish → rsyncs `site/` to
   the portfolio webroot on Forge (guison.net/recipes) → commits and pushes to
   GitHub. Intermediate artifacts (transcripts, OCR frames) are cleaned up per
   recipe; no video files are ever retained.

## The section page

`site/index.html` is a self-contained, JavaScript-free-to-render page:
- **Ingredient search**: type ingredients you have (comma or space separated);
  the library filters to recipes containing **all** of them.
- **Category chips**: multi-select over the fixed category list.
- Cards render server-side so the page works without JS; `search.js` only
  filters on top.

## Layout

```
pipeline/    ingestion + publish scripts
recipes/     generated Markdown recipes (PII-scrubbed before publishing)
site/        static section: index.html (search page), <slug>.html, data.json, search.js
scripts/     the cron entrypoint
```

## Privacy

Recipe output is scrubbed before publication (email addresses, local paths,
names, phone numbers) and any recipe that still looks like it leaks personal
information is skipped rather than published. Source links point at the public
social/blog post the recipe came from.

## Requirements

- Python 3.10+
- Local Ollama with a chat model (e.g. `qwen2.5:3b`)
- `yt-dlp` for YouTube transcripts
- `easyocr` (CPU) for carousel OCR
- Passwordless `ssh forge-ts` (Tailscale) to sync the section to the portfolio