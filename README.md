# TranscriptBot - YouTube Channel Transcript Archiver

Archive every transcript from any YouTube channel and search through them with keyword sentiment analysis.

## Quick Start

```bash
chmod +x run.sh
./run.sh
```

Then open **http://localhost:5000** in your browser.

## What It Does

1. **Archive** - Enter any YouTube channel URL and click "Archive". TranscriptBot fetches every video on the channel and downloads all available transcripts.
2. **Search** - Type any keyword to search across all archived transcripts. Results show matching video snippets with highlighted keywords.
3. **Sentiment Analysis** - Each search result includes sentiment analysis showing whether the keyword is discussed positively, negatively, or neutrally, with context sentences.

## Setup

### Requirements

- Python 3.8+
- A YouTube Data API v3 key ([Get one here](https://console.cloud.google.com/apis/credentials))

### First Run

1. Run `./run.sh` (installs everything automatically)
2. Open http://localhost:5000
3. Click **Settings** and paste your YouTube API key
4. Enter a channel URL and click **Archive**

### Manual Setup (if you prefer)

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 app.py
```

## Supported URL Formats

- `https://www.youtube.com/@ChannelHandle`
- `https://www.youtube.com/channel/UCxxxxxxxx`
- `https://www.youtube.com/c/ChannelName`
- `https://www.youtube.com/user/Username`

## Data Storage

All data is stored locally in `data/transcripts.db` (SQLite). No data is sent anywhere except to the YouTube API to fetch video lists and transcripts.
