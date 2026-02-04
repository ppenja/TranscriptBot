import os
import re
import sqlite3
import json
import logging
from datetime import datetime
from threading import Thread

from flask import Flask, render_template, request, jsonify, g
from googleapiclient.discovery import build
from youtube_transcript_api import YouTubeTranscriptApi
from textblob import TextBlob
import nltk

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("transcriptbot")

# Ensure NLTK data is available
for _pkg in ("punkt_tab", "averaged_perceptron_tagger", "brown", "wordnet"):
    try:
        nltk.data.find(f"tokenizers/{_pkg}" if "punkt" in _pkg else f"corpora/{_pkg}")
    except LookupError:
        nltk.download(_pkg, quiet=True)

app = Flask(__name__)
DATABASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "transcripts.db")


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_db():
    if "db" not in g:
        os.makedirs(os.path.dirname(DATABASE), exist_ok=True)
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    """Create tables if they don't exist."""
    os.makedirs(os.path.dirname(DATABASE), exist_ok=True)
    db = sqlite3.connect(DATABASE)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS config (
            key   TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS channels (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id   TEXT UNIQUE NOT NULL,
            channel_name TEXT,
            channel_url  TEXT,
            archived_at  TEXT
        );

        CREATE TABLE IF NOT EXISTS videos (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id    TEXT NOT NULL,
            video_id      TEXT UNIQUE NOT NULL,
            title         TEXT,
            published_at  TEXT,
            thumbnail_url TEXT,
            FOREIGN KEY (channel_id) REFERENCES channels(channel_id)
        );

        CREATE TABLE IF NOT EXISTS transcripts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id    TEXT UNIQUE NOT NULL,
            full_text   TEXT,
            archived_at TEXT,
            FOREIGN KEY (video_id) REFERENCES videos(video_id)
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS transcripts_fts
            USING fts5(video_id, full_text, content='transcripts', content_rowid='id');

        -- Triggers to keep FTS in sync
        CREATE TRIGGER IF NOT EXISTS transcripts_ai AFTER INSERT ON transcripts BEGIN
            INSERT INTO transcripts_fts(rowid, video_id, full_text)
            VALUES (new.id, new.video_id, new.full_text);
        END;

        CREATE TRIGGER IF NOT EXISTS transcripts_ad AFTER DELETE ON transcripts BEGIN
            INSERT INTO transcripts_fts(transcripts_fts, rowid, video_id, full_text)
            VALUES ('delete', old.id, old.video_id, old.full_text);
        END;

        CREATE TRIGGER IF NOT EXISTS transcripts_au AFTER UPDATE ON transcripts BEGIN
            INSERT INTO transcripts_fts(transcripts_fts, rowid, video_id, full_text)
            VALUES ('delete', old.id, old.video_id, old.full_text);
            INSERT INTO transcripts_fts(rowid, video_id, full_text)
            VALUES (new.id, new.video_id, new.full_text);
        END;
    """)
    db.commit()
    db.close()


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def get_config(key):
    db = get_db()
    row = db.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_config(key, value):
    db = get_db()
    db.execute(
        "INSERT INTO config (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    db.commit()


# ---------------------------------------------------------------------------
# YouTube helpers
# ---------------------------------------------------------------------------

def extract_channel_identifier(url):
    """Extract channel ID, handle, or username from various YouTube URL formats."""
    url = url.strip().rstrip("/")

    # @handle format: youtube.com/@handle
    m = re.search(r"youtube\.com/@([\w.-]+)", url)
    if m:
        return ("handle", m.group(1))

    # /channel/UC... format
    m = re.search(r"youtube\.com/channel/(UC[\w-]+)", url)
    if m:
        return ("channel_id", m.group(1))

    # /c/customname or /user/username
    m = re.search(r"youtube\.com/(?:c|user)/([\w.-]+)", url)
    if m:
        return ("username", m.group(1))

    # Plain channel ID
    if url.startswith("UC") and len(url) == 24:
        return ("channel_id", url)

    # Maybe just a handle without @
    m = re.search(r"youtube\.com/([\w.-]+)", url)
    if m and m.group(1) not in ("watch", "playlist", "feed", "results", "shorts"):
        return ("handle", m.group(1))

    return (None, None)


def resolve_channel_id(youtube, id_type, identifier):
    """Resolve various identifier types to an actual channel ID and name."""
    if id_type == "channel_id":
        resp = youtube.channels().list(part="snippet", id=identifier).execute()
        if resp.get("items"):
            return identifier, resp["items"][0]["snippet"]["title"]
        return None, None

    if id_type == "handle":
        # Try forHandle parameter first
        resp = youtube.channels().list(part="snippet", forHandle=identifier).execute()
        if resp.get("items"):
            item = resp["items"][0]
            return item["id"], item["snippet"]["title"]
        # Fallback: search
        resp = youtube.search().list(part="snippet", q=identifier, type="channel", maxResults=1).execute()
        if resp.get("items"):
            ch_id = resp["items"][0]["snippet"]["channelId"]
            return resolve_channel_id(youtube, "channel_id", ch_id)
        return None, None

    if id_type == "username":
        resp = youtube.channels().list(part="snippet", forUsername=identifier).execute()
        if resp.get("items"):
            item = resp["items"][0]
            return item["id"], item["snippet"]["title"]
        return None, None

    return None, None


def get_all_video_ids(youtube, channel_id):
    """Fetch all video IDs from a channel using the YouTube Data API."""
    video_ids = []

    # Get the uploads playlist
    resp = youtube.channels().list(part="contentDetails", id=channel_id).execute()
    if not resp.get("items"):
        return []
    uploads_playlist = resp["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]

    next_page = None
    while True:
        pl_resp = youtube.playlistItems().list(
            part="contentDetails",
            playlistId=uploads_playlist,
            maxResults=50,
            pageToken=next_page,
        ).execute()

        for item in pl_resp.get("items", []):
            video_ids.append(item["contentDetails"]["videoId"])

        next_page = pl_resp.get("nextPageToken")
        if not next_page:
            break

    return video_ids


def get_video_details(youtube, video_ids):
    """Fetch video metadata in batches of 50."""
    details = []
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i : i + 50]
        resp = youtube.videos().list(part="snippet", id=",".join(batch)).execute()
        for item in resp.get("items", []):
            details.append({
                "video_id": item["id"],
                "title": item["snippet"]["title"],
                "published_at": item["snippet"]["publishedAt"],
                "thumbnail_url": item["snippet"]["thumbnails"].get("medium", {}).get("url", ""),
                "channel_id": item["snippet"]["channelId"],
            })
    return details


def fetch_transcript(video_id):
    """Download transcript for a single video. Returns text or None."""
    try:
        ytt = YouTubeTranscriptApi()
        transcript = ytt.fetch(video_id)
        return " ".join(snippet.text for snippet in transcript)
    except Exception as e:
        log.warning("Transcript unavailable for %s: %s", video_id, e)
        return None


# ---------------------------------------------------------------------------
# Archiving – runs in a background thread
# ---------------------------------------------------------------------------

# Simple in-memory job status tracker
archive_jobs = {}


def archive_channel_task(job_id, channel_url, api_key):
    """Background task that archives all transcripts for a channel."""
    # We need our own DB connection since we're in a separate thread
    os.makedirs(os.path.dirname(DATABASE), exist_ok=True)
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")

    try:
        archive_jobs[job_id]["status"] = "resolving_channel"
        youtube = build("youtube", "v3", developerKey=api_key)

        id_type, identifier = extract_channel_identifier(channel_url)
        if not id_type:
            archive_jobs[job_id]["status"] = "error"
            archive_jobs[job_id]["error"] = "Could not parse the YouTube channel URL. Please use a URL like youtube.com/@ChannelName or youtube.com/channel/UC..."
            db.close()
            return

        channel_id, channel_name = resolve_channel_id(youtube, id_type, identifier)
        if not channel_id:
            archive_jobs[job_id]["status"] = "error"
            archive_jobs[job_id]["error"] = "Could not find that YouTube channel. Please check the URL and try again."
            db.close()
            return

        archive_jobs[job_id]["channel_name"] = channel_name
        archive_jobs[job_id]["status"] = "fetching_videos"

        # Save channel
        db.execute(
            "INSERT OR IGNORE INTO channels (channel_id, channel_name, channel_url, archived_at) VALUES (?, ?, ?, ?)",
            (channel_id, channel_name, channel_url, datetime.utcnow().isoformat()),
        )
        db.commit()

        # Get all video IDs
        video_ids = get_all_video_ids(youtube, channel_id)
        archive_jobs[job_id]["total_videos"] = len(video_ids)
        archive_jobs[job_id]["status"] = "fetching_details"

        # Get video details
        videos = get_video_details(youtube, video_ids)

        # Store video metadata
        for v in videos:
            db.execute(
                "INSERT OR IGNORE INTO videos (channel_id, video_id, title, published_at, thumbnail_url) VALUES (?, ?, ?, ?, ?)",
                (v["channel_id"], v["video_id"], v["title"], v["published_at"], v["thumbnail_url"]),
            )
        db.commit()

        archive_jobs[job_id]["status"] = "downloading_transcripts"
        archive_jobs[job_id]["completed"] = 0
        archive_jobs[job_id]["skipped"] = 0
        archive_jobs[job_id]["failed"] = 0
        archive_jobs[job_id]["saved"] = 0

        for v in videos:
            # Skip if we already have the transcript
            existing = db.execute("SELECT id FROM transcripts WHERE video_id = ?", (v["video_id"],)).fetchone()
            if existing:
                archive_jobs[job_id]["completed"] += 1
                archive_jobs[job_id]["skipped"] += 1
                continue

            text = fetch_transcript(v["video_id"])
            if text:
                db.execute(
                    "INSERT INTO transcripts (video_id, full_text, archived_at) VALUES (?, ?, ?)",
                    (v["video_id"], text, datetime.utcnow().isoformat()),
                )
                db.commit()
                archive_jobs[job_id]["saved"] += 1
            else:
                archive_jobs[job_id]["failed"] += 1

            archive_jobs[job_id]["completed"] += 1

        archive_jobs[job_id]["status"] = "done"

    except Exception as e:
        archive_jobs[job_id]["status"] = "error"
        archive_jobs[job_id]["error"] = str(e)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Sentiment analysis
# ---------------------------------------------------------------------------

def analyze_sentiment(text, keyword):
    """Find sentences containing the keyword and analyze sentiment for each."""
    sentences = TextBlob(text).sentences
    results = []
    keyword_lower = keyword.lower()

    for sentence in sentences:
        if keyword_lower in sentence.lower():
            polarity = sentence.sentiment.polarity
            if polarity > 0.1:
                label = "positive"
            elif polarity < -0.1:
                label = "negative"
            else:
                label = "neutral"
            results.append({
                "sentence": str(sentence),
                "polarity": round(polarity, 3),
                "label": label,
            })

    return results


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    if request.method == "POST":
        data = request.get_json()
        api_key = data.get("api_key", "").strip()
        if not api_key:
            return jsonify({"error": "API key is required"}), 400
        set_config("youtube_api_key", api_key)
        return jsonify({"ok": True})

    key = get_config("youtube_api_key")
    return jsonify({"has_key": bool(key), "key_preview": f"{key[:8]}...{key[-4:]}" if key and len(key) > 12 else ""})


@app.route("/api/archive", methods=["POST"])
def api_archive():
    data = request.get_json()
    channel_url = data.get("channel_url", "").strip()
    if not channel_url:
        return jsonify({"error": "Channel URL is required"}), 400

    api_key = get_config("youtube_api_key")
    if not api_key:
        return jsonify({"error": "YouTube API key not configured. Please set it in Settings."}), 400

    job_id = f"job_{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}"
    archive_jobs[job_id] = {
        "status": "starting",
        "channel_url": channel_url,
        "channel_name": "",
        "total_videos": 0,
        "completed": 0,
        "skipped": 0,
        "error": None,
    }

    thread = Thread(target=archive_channel_task, args=(job_id, channel_url, api_key), daemon=True)
    thread.start()

    return jsonify({"job_id": job_id})


@app.route("/api/archive/status/<job_id>")
def api_archive_status(job_id):
    job = archive_jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job)


@app.route("/api/channels")
def api_channels():
    db = get_db()
    channels = db.execute("SELECT * FROM channels ORDER BY archived_at DESC").fetchall()
    result = []
    for ch in channels:
        video_count = db.execute("SELECT COUNT(*) as cnt FROM videos WHERE channel_id = ?", (ch["channel_id"],)).fetchone()["cnt"]
        transcript_count = db.execute(
            "SELECT COUNT(*) as cnt FROM transcripts t JOIN videos v ON t.video_id = v.video_id WHERE v.channel_id = ?",
            (ch["channel_id"],),
        ).fetchone()["cnt"]
        result.append({
            "channel_id": ch["channel_id"],
            "channel_name": ch["channel_name"],
            "channel_url": ch["channel_url"],
            "archived_at": ch["archived_at"],
            "video_count": video_count,
            "transcript_count": transcript_count,
        })
    return jsonify(result)


@app.route("/api/search")
def api_search():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"error": "Search query is required"}), 400

    db = get_db()

    # Use FTS5 for fast full-text search
    # Build a safe FTS query: wrap each word in double-quotes so special chars are escaped
    words = query.split()
    fts_query = " ".join(f'"{w}"' for w in words)

    rows = db.execute("""
        SELECT
            f.video_id,
            snippet(transcripts_fts, 1, '<mark>', '</mark>', '...', 40) as snippet,
            v.title,
            v.thumbnail_url,
            v.published_at,
            c.channel_name
        FROM transcripts_fts f
        JOIN videos v ON f.video_id = v.video_id
        JOIN channels c ON v.channel_id = c.channel_id
        WHERE transcripts_fts MATCH ?
        ORDER BY rank
        LIMIT 100
    """, (fts_query,)).fetchall()

    results = []
    for row in rows:
        # Get the full transcript for sentiment analysis
        transcript = db.execute("SELECT full_text FROM transcripts WHERE video_id = ?", (row["video_id"],)).fetchone()
        sentiment_data = []
        if transcript:
            sentiment_data = analyze_sentiment(transcript["full_text"], query)

        # Compute overall sentiment
        if sentiment_data:
            avg_polarity = sum(s["polarity"] for s in sentiment_data) / len(sentiment_data)
            if avg_polarity > 0.1:
                overall = "positive"
            elif avg_polarity < -0.1:
                overall = "negative"
            else:
                overall = "neutral"
        else:
            avg_polarity = 0
            overall = "neutral"

        results.append({
            "video_id": row["video_id"],
            "title": row["title"],
            "thumbnail_url": row["thumbnail_url"],
            "published_at": row["published_at"],
            "channel_name": row["channel_name"],
            "snippet": row["snippet"],
            "sentiment": {
                "overall": overall,
                "polarity": round(avg_polarity, 3),
                "details": sentiment_data[:5],  # Top 5 context sentences
            },
        })

    return jsonify({"query": query, "count": len(results), "results": results})


@app.route("/api/stats")
def api_stats():
    db = get_db()
    channels = db.execute("SELECT COUNT(*) as cnt FROM channels").fetchone()["cnt"]
    videos = db.execute("SELECT COUNT(*) as cnt FROM videos").fetchone()["cnt"]
    transcripts = db.execute("SELECT COUNT(*) as cnt FROM transcripts").fetchone()["cnt"]
    return jsonify({"channels": channels, "videos": videos, "transcripts": transcripts})


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    init_db()
    app.run(debug=True, host="0.0.0.0", port=5000)
