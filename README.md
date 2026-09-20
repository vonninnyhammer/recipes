# Recipes

A small, self-hosted recipe collector. A cron job watches a local mail folder,
pulls recipe links out of social-media share emails (Instagram, TikTok,
Facebook Reels, YouTube Shorts), fetches the page/transcript/carousel text, and
renders each into a clean, structured Markdown recipe that gets published here
and as a static site.

## How it works

1. Emails land in a local **Recipes** mail folder (moved there manually or by a
   Thunderbird filter).
2. `pipeline/recipe-ingest.py` scans the folder, extracts the shared URL,
   fetches clean text via the Jina Reader, follows in-post "full recipe" links,
   pulls YouTube auto-captions with `yt-dlp`, and OCRs carousel images with
   EasyOCR when needed.
3. Text is formatted into a standard recipe template (YAML frontmatter +
   Ingredients + Instructions) by a local Ollama model (`qwen2.5:3b`), with a
   structural parser as fallback.
4. `pipeline/recipe-publish.py` scrubs the output for personal information,
   writes the Markdown to `recipes/`, and renders the static site in `site/`.

## Layout

```
pipeline/    ingestion + publish scripts
recipes/     generated Markdown recipes (PII-scrubbed before publishing)
site/        static HTML site (index + per-recipe pages)
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