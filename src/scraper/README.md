# Discord Resume Channel Scraper

A Python script that uses **your Discord account** to scrape resume posts and critique replies from a specific channel, download attachments, store everything in SQLite, and export a clean dataset for AI analysis.

> **Warning:** Automating a user account (self-botting) violates [Discord's Terms of Service](https://discord.com/terms). Your account could be restricted or banned. Use at your own risk, preferably on a server you own or manage, and only for personal data export.

## Features

- Scrapes resume posts (PNG, JPG, JPEG, WEBP, PDF) from one configured channel
- Downloads attachments to `data/resumes/{message_id}/`
- Collects critique replies from linked threads (active and archived)
- Logs everything to SQLite
- Exports `data/export/dataset.json` plus one folder per resume

## Requirements

- Python 3.11+
- Your Discord user token (see setup below)
- Access to the target channel

## Setup

### 1. Configure environment

```bash
cp .env.example .env
```

Fill in `.env`:

```env
DISCORD_USER_TOKEN=your_user_token
RESUME_CHANNEL_ID=123456789012345678
DATA_DIR=./data
```

**Getting your user token:** Open Discord in a browser, open DevTools (Network tab), reload, click any Discord API request, and copy the `Authorization` header value from the request headers. Keep this secret — it grants full access to your account.

**Getting the channel ID:** Enable Developer Mode in Discord settings, then right-click the channel and choose "Copy Channel ID".

### 2. Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Usage

### Scrape the channel

Fetches resume posts and critique replies. Safe to run multiple times — already-recorded messages are skipped and **already-downloaded files are not re-downloaded**.

```bash
# Download up to 20 new resumes this run, then stop
python -m bot.main scrape --limit 20

# Only fetch critiques for resumes already downloaded (fast, no re-downloads)
python -m bot.main scrape --skip-download

# No limit (process everything)
python -m bot.main scrape
```

Each run with `--limit` picks up where you left off: resumes already in the database are skipped and do not count toward the limit. Use `--skip-download` to collect critiques without downloading any resume files.

### Export the dataset

```bash
python -m bot.main export
```

Generates:

```text
data/export/
  dataset.json
  {resume_message_id}/
    resume.pdf
    post.txt        # original message from the person who posted
    critiques.txt
```

After export, copied resume files are removed from `data/resumes/` to avoid duplicates. The export folder is the canonical copy.

`dataset.json` contains one object per resume:

```json
{
  "resume_message_id": "123",
  "author": "Alice",
  "posted_at": "2026-07-06T12:00:00+00:00",
  "post_message": "Looking for SWE internships, any feedback welcome!",
  "resume_files": ["data/export/123/resume.pdf"],
  "critiques": [
    {
      "author": "Bob",
      "content": "Strong projects section.",
      "timestamp": "2026-07-06T12:05:00+00:00"
    }
  ]
}
```

## Project structure

```text
bot/
  main.py         # CLI entry point (scrape / export)
  client.py       # Discord REST API client
  scraper.py      # channel + thread scraping logic
  db.py           # sqlite helpers
  export.py       # dataset export
  config.py       # env loading
  attachments.py  # attachment filtering/downloading
data/
  resumes/        # downloaded attachments
  export/         # export output
  bot.db          # sqlite database
.env
requirements.txt
```

## Database schema

```sql
CREATE TABLE resumes (
    message_id TEXT PRIMARY KEY,
    author_id TEXT,
    author_name TEXT,
    thread_id TEXT,
    posted_at TEXT,
    attachment_paths TEXT
);

CREATE TABLE critiques (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    resume_message_id TEXT REFERENCES resumes(message_id),
    message_id TEXT,
    author_id TEXT,
    author_name TEXT,
    content TEXT,
    posted_at TEXT
);
```

## Notes

- The script sleeps briefly between API calls to reduce rate-limit risk
- Messages without supported attachments in the resume channel are ignored
- Critique messages with only attachments store attachment URLs in the content field
- Users should redact their own PII before posting; this script does not perform OCR or PII detection
- Re-run `scrape` periodically to pick up new posts, then `export` when you want an updated dataset
