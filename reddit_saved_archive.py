#!/usr/bin/env python3

import argparse
import logging
import os
import re
import sqlite3
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

import praw
from praw.models import Comment, Submission

DEFAULT_CONFIG = Path("~/.config/reddit-saved-archive/config.toml").expanduser()
MARKER_RE = re.compile(r"<!-- reddit-archive:(t[13]_[A-Za-z0-9]+) -->")


def load_config(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(
            f"Config file not found: {path}\n"
            "Create it from config.example.toml and add your Reddit credentials."
        )
    with path.open("rb") as handle:
        return tomllib.load(handle)


def sanitise_path_component(value: str) -> str:
    value = value.strip()
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value)
    value = value.strip("._")
    return value or "_unknown"


def clean_heading(value: str) -> str:
    return " ".join((value or "").split()).strip()


def reddit_fullname(item) -> str:
    if isinstance(item, Submission):
        return f"t3_{item.id}"
    if isinstance(item, Comment):
        return f"t1_{item.id}"
    raise TypeError(f"Unsupported Reddit object: {type(item)!r}")


def author_name(item) -> str:
    try:
        return str(item.author) if item.author is not None else "[deleted]"
    except Exception:
        return "[unknown]"


def subreddit_name(item) -> str:
    try:
        subreddit = getattr(item, "subreddit", None)
        return str(subreddit) if subreddit is not None else "_unknown"
    except Exception:
        return "_unknown"


def reddit_permalink(item) -> str:
    permalink = getattr(item, "permalink", "") or ""
    if permalink.startswith("http://") or permalink.startswith("https://"):
        return permalink
    return f"https://www.reddit.com{permalink}"


def setup_logging(root: Path) -> logging.Logger:
    logger = logging.getLogger("reddit-saved-archive")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)

    file_handler = logging.FileHandler(root / "archive.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


def open_database(root: Path) -> sqlite3.Connection:
    database = sqlite3.connect(root / ".reddit_saved_archive.sqlite3")
    database.execute(
        """
        CREATE TABLE IF NOT EXISTS archived_items (
            fullname TEXT PRIMARY KEY,
            reddit_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            subreddit TEXT NOT NULL,
            created_utc REAL NOT NULL,
            archived_at TEXT NOT NULL,
            markdown_path TEXT NOT NULL,
            permalink TEXT NOT NULL
        )
        """
    )
    database.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_archived_subreddit
        ON archived_items(subreddit)
        """
    )
    database.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_archived_created
        ON archived_items(created_utc)
        """
    )
    database.commit()
    return database


def is_in_database(database: sqlite3.Connection, fullname: str) -> bool:
    return database.execute(
        "SELECT 1 FROM archived_items WHERE fullname = ?",
        (fullname,),
    ).fetchone() is not None


def register_item(
    database: sqlite3.Connection,
    item,
    fullname: str,
    kind: str,
    subreddit: str,
    markdown_path: Path,
) -> None:
    database.execute(
        """
        INSERT OR IGNORE INTO archived_items (
            fullname, reddit_id, kind, subreddit, created_utc,
            archived_at, markdown_path, permalink
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            fullname,
            item.id,
            kind,
            subreddit,
            float(item.created_utc),
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
            str(markdown_path),
            reddit_permalink(item),
        ),
    )
    database.commit()


def destination_for(item, root: Path):
    created = datetime.fromtimestamp(float(item.created_utc), tz=timezone.utc)
    subreddit = subreddit_name(item)
    safe_subreddit = sanitise_path_component(subreddit)
    folder = root / str(created.year) / safe_subreddit
    filename = f"{created.year}-{created.month:02d}.md"
    return folder / filename, subreddit, created


def build_submission_markdown(
    item: Submission,
    fullname: str,
    subreddit: str,
    created: datetime,
) -> str:
    title = clean_heading(getattr(item, "title", "")) or "[Untitled post]"
    author = author_name(item)
    permalink = reddit_permalink(item)
    author_line = (
        f"**Author:** u/{author}  "
        if not author.startswith("[")
        else f"**Author:** {author}  "
    )

    lines = [
        f"<!-- reddit-archive:{fullname} -->",
        "",
        f"## {title}",
        "",
        "**Type:** Post  ",
        f"**Subreddit:** r/{subreddit}  ",
        author_line,
        f"**Created:** {created.isoformat(timespec='seconds')}  ",
        f"**Reddit ID:** {fullname}  ",
        f"**Reddit:** {permalink}  ",
    ]

    external_url = getattr(item, "url", None)
    if external_url and external_url != permalink and not getattr(item, "is_self", False):
        lines.append(f"**Linked URL:** {external_url}  ")

    selftext = getattr(item, "selftext", "") or ""
    if selftext.strip():
        lines.extend(["", "### Content", "", selftext.rstrip()])

    lines.extend(["", "---", ""])
    return "\n".join(lines)


def build_comment_markdown(
    item: Comment,
    fullname: str,
    subreddit: str,
    created: datetime,
) -> str:
    author = author_name(item)
    permalink = reddit_permalink(item)

    try:
        post_title = item.__dict__.get("link_title")
    except Exception:
        post_title = None

    if post_title:
        heading = f"Comment on: {clean_heading(post_title)}"
    elif author.startswith("["):
        heading = "Saved comment"
    else:
        heading = f"Saved comment by u/{author}"

    author_line = (
        f"**Author:** u/{author}  "
        if not author.startswith("[")
        else f"**Author:** {author}  "
    )
    body = getattr(item, "body", "") or "[comment unavailable]"

    lines = [
        f"<!-- reddit-archive:{fullname} -->",
        "",
        f"## {heading}",
        "",
        "**Type:** Comment  ",
        f"**Subreddit:** r/{subreddit}  ",
        author_line,
        f"**Created:** {created.isoformat(timespec='seconds')}  ",
        f"**Reddit ID:** {fullname}  ",
        f"**Reddit:** {permalink}  ",
        "",
        "### Comment",
        "",
        body.rstrip(),
        "",
        "---",
        "",
    ]
    return "\n".join(lines)


class MarkerCache:
    def __init__(self):
        self.cache: dict[Path, set[str]] = {}

    def markers_for(self, path: Path) -> set[str]:
        if path in self.cache:
            return self.cache[path]

        if not path.exists():
            markers: set[str] = set()
        else:
            text = path.read_text(encoding="utf-8", errors="replace")
            markers = set(MARKER_RE.findall(text))

        self.cache[path] = markers
        return markers

    def contains(self, path: Path, fullname: str) -> bool:
        return fullname in self.markers_for(path)

    def add(self, path: Path, fullname: str) -> None:
        self.markers_for(path).add(fullname)


def append_markdown(
    path: Path,
    subreddit: str,
    created: datetime,
    content: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists()

    with path.open("a", encoding="utf-8") as handle:
        if is_new:
            handle.write(f"# r/{subreddit} — {created.year}-{created.month:02d}\n\n")

        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def archive_item(
    item,
    root: Path,
    database: sqlite3.Connection,
    marker_cache: MarkerCache,
    logger: logging.Logger,
) -> str:
    fullname = reddit_fullname(item)

    if is_in_database(database, fullname):
        return "existing"

    path, subreddit, created = destination_for(item, root)

    if marker_cache.contains(path, fullname):
        kind = "post" if isinstance(item, Submission) else "comment"
        register_item(database, item, fullname, kind, subreddit, path)
        logger.info("Recovered DB entry for %s", fullname)
        return "recovered"

    if isinstance(item, Submission):
        kind = "post"
        markdown = build_submission_markdown(item, fullname, subreddit, created)
    elif isinstance(item, Comment):
        kind = "comment"
        markdown = build_comment_markdown(item, fullname, subreddit, created)
    else:
        logger.warning("Skipping unsupported item type: %r", type(item))
        return "unsupported"

    append_markdown(path, subreddit, created, markdown)
    marker_cache.add(path, fullname)
    register_item(database, item, fullname, kind, subreddit, path)

    logger.info("Archived %-7s %-20s %s", kind, f"r/{subreddit}", fullname)
    return "archived"


def create_reddit(config: dict):
    reddit_config = config["reddit"]
    username = reddit_config["username"]
    user_agent = reddit_config.get(
        "user_agent",
        f"linux:reddit-saved-archive:v0.1 (by /u/{username})",
    )

    return praw.Reddit(
        client_id=reddit_config["client_id"],
        client_secret=reddit_config["client_secret"],
        username=username,
        password=reddit_config["password"],
        user_agent=user_agent,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Archive Reddit Saved posts/comments to local Markdown. "
            "Version 1 is read-only against Reddit."
        )
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help=f"Config file (default: {DEFAULT_CONFIG})",
    )
    parser.add_argument(
        "--root",
        type=Path,
        help="Override the archive directory from config.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only process this many Saved items.",
    )
    args = parser.parse_args()

    try:
        config = load_config(args.config.expanduser())
    except Exception as exc:
        print(exc, file=sys.stderr)
        return 1

    configured_root = config.get("archive", {}).get(
        "root",
        "~/Documents/Reddit Archive",
    )

    root = args.root.expanduser() if args.root else Path(configured_root).expanduser()
    root.mkdir(parents=True, exist_ok=True)

    logger = setup_logging(root)
    database = open_database(root)

    try:
        reddit = create_reddit(config)
        me = reddit.user.me()
        if me is None:
            raise RuntimeError("Authentication completed without an authenticated Reddit user.")
        logger.info("Authenticated as u/%s", me.name)
    except Exception:
        logger.exception("Reddit authentication failed")
        database.close()
        return 1

    marker_cache = MarkerCache()
    stats = {
        "seen": 0,
        "archived": 0,
        "existing": 0,
        "recovered": 0,
        "unsupported": 0,
        "errors": 0,
    }

    logger.info("Reading Reddit Saved items...")

    try:
        for item in reddit.user.me().saved(limit=args.limit):
            stats["seen"] += 1
            try:
                result = archive_item(
                    item,
                    root,
                    database,
                    marker_cache,
                    logger,
                )
                stats[result] += 1
            except KeyboardInterrupt:
                raise
            except Exception:
                stats["errors"] += 1
                try:
                    item_id = reddit_fullname(item)
                except Exception:
                    item_id = "unknown"
                logger.exception("Failed to archive %s", item_id)

    except KeyboardInterrupt:
        logger.warning("Interrupted by user.")
        return_code = 130
    except Exception:
        logger.exception("Failed while reading Reddit Saved listing.")
        return_code = 1
    else:
        return_code = 0
    finally:
        database.close()

    print()
    print("Finished.")
    print(f"  Seen:             {stats['seen']}")
    print(f"  Newly archived:   {stats['archived']}")
    print(f"  Already archived: {stats['existing']}")
    print(f"  DB recovered:     {stats['recovered']}")
    print(f"  Unsupported:      {stats['unsupported']}")
    print(f"  Errors:           {stats['errors']}")

    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
