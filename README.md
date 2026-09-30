# Reddit Saved Archive

A small personal, non-commercial Python utility that archives the authenticated user's saved Reddit posts and comments to local Markdown files.

**Version 1 is read-only against Reddit.** It does not unsave, vote on, edit, comment on, or otherwise modify Reddit content.

## Purpose

Reddit's Saved list is useful as an inbox, but less convenient as a long-term archive. This tool copies saved posts and comments into a local folder structure organised by the Reddit item's original creation date and subreddit:

```text
Reddit Archive/
├── 2025/
│   └── woodworking/
│       └── 2025-08.md
├── 2026/
│   └── X4Foundations/
│       └── 2026-09.md
├── .reddit_saved_archive.sqlite3
└── archive.log
```

## What v1 does

- Authenticates to Reddit with PRAW.
- Reads the authenticated user's Saved listing.
- Handles saved posts and saved comments.
- Stores entries under `YEAR/SUBREDDIT/YYYY-MM.md`.
- Uses the Reddit item's original creation date.
- Preserves useful metadata, Reddit permalinks, post text, linked URLs, and comment bodies.
- Uses SQLite plus embedded Markdown markers for deduplication across repeated runs.
- Supports `--limit` for cautious first runs.

## What v1 deliberately does not do

- It does **not** call Reddit's unsave endpoint.
- It does **not** modify Saved state.
- It does **not** vote, post, comment, edit, delete, message, or moderate anything.
- It does not download linked media yet.

## Requirements

- Python 3.11+
- [PRAW](https://praw.readthedocs.io/)

```bash
python -m venv .venv
source .venv/bin/activate
pip install praw
```

## Configuration

Copy `config.example.toml` to:

```text
~/.config/reddit-saved-archive/config.toml
```

Then add the credentials for your approved Reddit OAuth application and restrict the file:

```bash
chmod 600 ~/.config/reddit-saved-archive/config.toml
```

**Never commit your real configuration or Reddit credentials.**

## First test

```bash
python reddit_saved_archive.py --limit 20
```

Inspect the generated Markdown and run the same command again. The second run should report those items as already archived rather than duplicating them.

When satisfied:

```bash
python reddit_saved_archive.py
```

## Safety and data handling

This program is intended for personal use against the authenticated user's own Saved listing. Credentials remain in a local config file. Archived content is written only to the user's local filesystem.

This project requires authenticated Reddit Data API access because it needs the user's private Saved listing. It uses PRAW/OAuth rather than scraping.

A later version may optionally support removing an item from Reddit Saved only after it has been safely archived. That behaviour is intentionally absent from v1.
