#!/usr/bin/env python3
"""
claude-lens: Weekly digest of Claude/Anthropic updates
Sources:
  1. YouTube @claude channel transcripts (via youtube-transcript-api + yt-dlp)
  2. Nate Jones transcript index on GitHub (204 Anthropic-tagged episodes)
  3. github.com/anthropics/claude-code releases.atom
  4. taobojlen/anthropic-rss-feed (News + Engineering blog)

Run: python3 ~/.claude/scripts/claude-lens.py [--since 7] [--output ~/claude-updates]
"""
import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
import yt_dlp

try:
    from youtube_transcript_api import YouTubeTranscriptApi, NoTranscriptFound, TranscriptsDisabled
    HAS_YT_TRANSCRIPT = True
except ImportError:
    HAS_YT_TRANSCRIPT = False

# ── Constants ─────────────────────────────────────────────────────────────────

CLAUDE_YT_CHANNEL = "https://www.youtube.com/@claude/videos"

NATE_JONES_INDEXES = {
    "anthropic": "https://raw.githubusercontent.com/kani3894/nate-jones-transcripts/main/index/anthropic.md",
    "claude": "https://raw.githubusercontent.com/kani3894/nate-jones-transcripts/main/index/claude.md",
}

FEEDS = {
    "claude-code-releases": "https://github.com/anthropics/claude-code/releases.atom",
    "anthropic-news": "https://raw.githubusercontent.com/taobojlen/anthropic-rss-feed/main/anthropic_news_rss.xml",
    "anthropic-engineering": "https://raw.githubusercontent.com/taobojlen/anthropic-rss-feed/main/anthropic_engineering_rss.xml",
}

# Relevant terms for filtering — focused on dev/vibecoding/marketing
RELEVANT_TERMS = [
    "claude code", "skill", "hook", "subagent", "agent", "mcp", "tool use",
    "api", "prompt", "release", "feature", "model", "sonnet", "opus", "haiku",
    "stream", "artifact", "computer use", "files api", "thinking", "extended thinking",
    "vibecod", "vibe cod", "marketing", "automation", "workflow", "context window",
    "token", "cache", "batch", "vision", "multimodal", "structured output",
    "claude.ai", "opus 4", "sonnet 4", "haiku 4",
]

SKIP_TERMS = [
    "safety research", "constitutional ai paper", "academic", "enterprise compliance",
    "red team", "rlhf", "alignment theory",
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def is_relevant(text: str) -> bool:
    t = text.lower()
    if any(s in t for s in SKIP_TERMS):
        return False
    return any(r in t for r in RELEVANT_TERMS)


def since_dt(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


def parse_dt(s: str) -> datetime | None:
    """Parse common date formats from feeds."""
    for fmt in (
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S+00:00",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%a, %d %b %Y %H:%M:%S +0000",
        "%a, %d %b %Y %H:%M:%S GMT",
    ):
        try:
            return datetime.strptime(s.strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


# ── Source 1: YouTube @claude ─────────────────────────────────────────────────

def fetch_youtube_videos(since: datetime, max_videos: int = 20) -> list[dict]:
    """List recent videos from @claude, fetch transcripts for relevant ones."""
    results = []
    ydl_opts = {
        "quiet": True,
        "extract_flat": True,
        "playlistend": max_videos,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(CLAUDE_YT_CHANNEL, download=False)
            entries = info.get("entries", []) if info else []
    except Exception as e:
        print(f"  [youtube] channel fetch failed: {e}", file=sys.stderr)
        return results

    for entry in entries:
        video_id = entry.get("id") or entry.get("url", "").split("?v=")[-1]
        title = entry.get("title", "")
        url = f"https://www.youtube.com/watch?v={video_id}"

        # yt-dlp flat extract doesn't always give upload date — try to get it
        upload_date_str = entry.get("upload_date")  # YYYYMMDD or None
        if upload_date_str:
            try:
                upload_dt = datetime.strptime(upload_date_str, "%Y%m%d").replace(tzinfo=timezone.utc)
                if upload_dt < since:
                    continue
            except ValueError:
                pass

        transcript_text = ""
        if HAS_YT_TRANSCRIPT and video_id:
            try:
                langs = ["en", "sv", "en-US"]
                transcript = YouTubeTranscriptApi.get_transcript(video_id, languages=langs)
                transcript_text = " ".join(t["text"] for t in transcript)
            except (NoTranscriptFound, TranscriptsDisabled):
                pass
            except Exception:
                pass

        combined = f"{title} {transcript_text[:500]}"
        if not is_relevant(combined):
            continue

        snippet = transcript_text[:300].replace("\n", " ") if transcript_text else "(no transcript)"
        results.append({
            "source": "YouTube @claude",
            "title": title,
            "url": url,
            "snippet": snippet,
            "date": upload_date_str or "unknown",
        })

    return results


# ── Source 2: Nate Jones transcript index ────────────────────────────────────

def fetch_nate_jones(since: datetime) -> list[dict]:
    """Pull episode index entries from kani3894/nate-jones-transcripts."""
    results = []
    cutoff_str = since.strftime("%Y-%m")  # "2026-04"

    for topic, url in NATE_JONES_INDEXES.items():
        try:
            r = requests.get(url, timeout=10)
            r.raise_for_status()
        except Exception as e:
            print(f"  [nate-jones] fetch failed for {topic}: {e}", file=sys.stderr)
            continue

        for line in r.text.splitlines():
            # Typical format: "- [2026-01-15] Episode title (link)"
            date_match = re.search(r"\[(\d{4}-\d{2}-\d{2})\]", line)
            if not date_match:
                continue
            ep_date = date_match.group(1)[:7]  # YYYY-MM
            if ep_date < cutoff_str:
                continue

            title_match = re.search(r"\]\s*(.+?)(?:\s*\(|$)", line)
            link_match = re.search(r"\((https?://[^\)]+)\)", line)
            title = title_match.group(1).strip() if title_match else line.strip()
            ep_url = link_match.group(1) if link_match else ""

            if not is_relevant(f"{title} anthropic claude"):
                continue

            results.append({
                "source": f"Nate Jones ({topic})",
                "title": title,
                "url": ep_url,
                "snippet": "",
                "date": date_match.group(1),
            })

    return results


# ── Source 3 & 4: RSS / Atom feeds ──────────────────────────────────────────

ATOM_NS = "http://www.w3.org/2005/Atom"


def fetch_feed(name: str, url: str, since: datetime) -> list[dict]:
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
    except Exception as e:
        print(f"  [feed:{name}] fetch failed: {e}", file=sys.stderr)
        return []

    try:
        root = ET.fromstring(r.content)
    except ET.ParseError as e:
        print(f"  [feed:{name}] parse error: {e}", file=sys.stderr)
        return []

    results = []
    ns = {"atom": ATOM_NS}

    # Atom format (claude-code releases)
    for entry in root.findall("atom:entry", ns) or root.findall("{%s}entry" % ATOM_NS):
        title_el = entry.find("{%s}title" % ATOM_NS)
        link_el = entry.find("{%s}link" % ATOM_NS)
        updated_el = entry.find("{%s}updated" % ATOM_NS)
        summary_el = entry.find("{%s}summary" % ATOM_NS) or entry.find("{%s}content" % ATOM_NS)

        title = title_el.text if title_el is not None else ""
        link = link_el.get("href", "") if link_el is not None else ""
        updated = updated_el.text if updated_el is not None else ""
        summary = (summary_el.text or "")[:400] if summary_el is not None else ""

        dt = parse_dt(updated)
        if dt and dt < since:
            continue

        if not is_relevant(f"{title} {summary}"):
            continue

        results.append({
            "source": name,
            "title": title,
            "url": link,
            "snippet": re.sub(r"<[^>]+>", "", summary)[:300],
            "date": updated[:10] if updated else "unknown",
        })

    # RSS format (anthropic feeds)
    channel = root.find("channel")
    if channel is not None:
        for item in channel.findall("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            pub_date = (item.findtext("pubDate") or "").strip()
            desc = (item.findtext("description") or "")[:400].strip()

            dt = parse_dt(pub_date)
            if dt and dt < since:
                continue

            if not is_relevant(f"{title} {desc}"):
                continue

            results.append({
                "source": name,
                "title": title,
                "url": link,
                "snippet": re.sub(r"<[^>]+>", "", desc)[:300],
                "date": pub_date[:16] if pub_date else "unknown",
            })

    return results


# ── Skill gap analysis ────────────────────────────────────────────────────────

NEW_FEATURES = {
    "Files API": ["files api", "file upload", "file reference"],
    "Extended thinking / interleaved thinking": ["extended thinking", "interleaved thinking"],
    "MCP connector in Messages API": ["mcp connector", "remote mcp"],
    "Code execution tool": ["code execution tool", "code tool"],
    "Structured outputs": ["structured output", "json schema"],
    "Agent Teams / multi-Claude": ["agent team", "multi-claude", "collaborative session"],
    "Computer use GA": ["computer use", "zoom action"],
    "Routines (scheduled sessions)": ["routine", "scheduled", "cron trigger"],
    "Tool Search Tool": ["tool search", "defer_loading"],
    "Background sessions": ["background session"],
    "Hooks system": ["hooks", "hook system", "precompact", "stop hook"],
    "Subagents": ["subagent", "sub-agent", "isolated context"],
}


def audit_skills(skills_dir: Path) -> list[str]:
    """Find new Claude features not yet covered by existing skills/commands."""
    existing_text = ""
    for md in skills_dir.rglob("*.md"):
        try:
            existing_text += md.read_text(errors="ignore").lower()
        except Exception:
            pass
    # Also check project skills if we're in a project
    project_skills = Path.cwd() / "skills"
    if project_skills.exists():
        for md in project_skills.rglob("*.md"):
            try:
                existing_text += md.read_text(errors="ignore").lower()
            except Exception:
                pass

    gaps = []
    for feature, terms in NEW_FEATURES.items():
        if not any(t in existing_text for t in terms):
            gaps.append(feature)
    return gaps


# ── Markdown output ───────────────────────────────────────────────────────────

def render_digest(items: list[dict], gaps: list[str], since_days: int) -> str:
    week = datetime.now().strftime("%Y-W%V")
    today = datetime.now().strftime("%Y-%m-%d")

    by_source: dict[str, list[dict]] = {}
    for item in items:
        by_source.setdefault(item["source"], []).append(item)

    lines = [
        f"# claude-lens digest — {week}",
        f"_Generated {today} · Looking back {since_days} days_\n",
    ]

    if not items:
        lines.append("_Inga relevanta uppdateringar hittades under perioden._\n")
    else:
        for source, entries in by_source.items():
            lines.append(f"## {source} ({len(entries)})\n")
            for e in entries:
                lines.append(f"### [{e['title']}]({e['url']})")
                lines.append(f"__{e['date']}__")
                if e["snippet"]:
                    lines.append(f"> {e['snippet']}")
                lines.append("")

    if gaps:
        lines.append("## Skill-gap — features du saknar workflows för\n")
        for g in gaps:
            lines.append(f"- [ ] **{g}**")
        lines.append("")

    lines.append("---")
    lines.append("_Källor: YouTube @claude · Nate Jones transcripts · claude-code releases.atom · Anthropic RSS_")
    return "\n".join(lines)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="claude-lens: Weekly Claude/Anthropic digest")
    parser.add_argument("--since", type=int, default=7, help="Days to look back (default: 7)")
    parser.add_argument("--output", type=Path, default=Path.home() / "claude-updates",
                        help="Output directory (default: ~/claude-updates)")
    parser.add_argument("--no-youtube", action="store_true", help="Skip YouTube (faster)")
    parser.add_argument("--no-skills-audit", action="store_true", help="Skip skill gap analysis")
    parser.add_argument("--print", dest="print_only", action="store_true",
                        help="Print to stdout instead of saving file")
    args = parser.parse_args()

    cutoff = since_dt(args.since)
    skills_dir = Path.home() / ".claude" / "skills"

    print(f"claude-lens: fetching updates since {cutoff.date()} …", file=sys.stderr)

    all_items: list[dict] = []

    # YouTube
    if not args.no_youtube:
        print("  → YouTube @claude …", file=sys.stderr)
        all_items.extend(fetch_youtube_videos(cutoff))

    # Nate Jones
    print("  → Nate Jones transcripts …", file=sys.stderr)
    all_items.extend(fetch_nate_jones(cutoff))

    # Feeds
    for name, url in FEEDS.items():
        print(f"  → feed: {name} …", file=sys.stderr)
        all_items.extend(fetch_feed(name, url, cutoff))

    # Deduplicate by URL
    seen: set[str] = set()
    unique = []
    for item in all_items:
        key = item["url"] or item["title"]
        if key not in seen:
            seen.add(key)
            unique.append(item)

    # Skill gap audit
    gaps: list[str] = []
    if not args.no_skills_audit:
        print("  → skill gap audit …", file=sys.stderr)
        gaps = audit_skills(skills_dir)

    digest = render_digest(unique, gaps, args.since)

    if args.print_only:
        print(digest)
        return

    args.output.mkdir(parents=True, exist_ok=True)
    week = datetime.now().strftime("%Y-W%V")
    out_file = args.output / f"{week}.md"
    out_file.write_text(digest, encoding="utf-8")
    print(f"\nSparat: {out_file}", file=sys.stderr)
    print(f"  {len(unique)} uppdateringar · {len(gaps)} skill-gaps", file=sys.stderr)


if __name__ == "__main__":
    main()
