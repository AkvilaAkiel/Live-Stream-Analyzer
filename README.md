# Live Stream Analyzer

A headless Python and FFmpeg service for monitoring OTT live streams. It correlates video anomalies with audio activity, records incident evidence, and can notify operators through Telegram.

- **Detection:** black frames, freezes, prolonged silence, bursts of decoder errors, and unavailable sources. A freeze with audio uses a different threshold from a freeze with silence.
- **Operation:** one monitor per enabled source, configurable thresholds, hot reload of stream and threshold files, and watchdog retries.
- **Incident evidence:** UTC console logs, incident logs, and screenshots when a frame is available. Telegram is optional.

## Try it with synthetic video

Requires Docker with the Compose plugin. Run these commands from the repository root.

```sh
cp .env.example .env
cp config/config.example.json config/config.json
cp config/streams.example.json config/streams.json
docker compose build
docker compose run --rm --no-deps stream-analyzer ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i color=c=blue:s=320x180:r=25 \
  -f lavfi -i anullsrc=r=48000:cl=stereo \
  -t 25 -c:v mpeg4 -c:a aac /app/config/demo.mp4
docker compose up -d
docker compose logs -f stream-analyzer
```

The service loops a static blue video with silent audio in real time. Look for `MUTE` after about 2 seconds and `FREEZE` after about 6 seconds. Evidence is written under `screenshots/`. Press Ctrl+C to leave the log view, then run `docker compose down` to stop the demo.

## Monitor your own streams

- In `config/streams.json`, set each channel's `name` and `url`. The optional fields are `cdn_id`, `audio` and `enabled`.
- In `config/config.json`, use `defaults` for shared thresholds and `channels` for per-channel overrides keyed by name.
- Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env` to enable alerts. Alerts can send channel labels and incident screenshots to the configured chat.
- To sync a channel list from GitHub, set `GITHUB_RAW_URL` and, if needed, `GITHUB_TOKEN`. Authenticated requests require an HTTPS URL on `raw.githubusercontent.com` and do not follow redirects.

Keep credentials, stream lists, and incident artifacts local. Use only sources you are authorized to monitor. Treat diagnostic logs and screenshots as operational data.

## Desktop edition

The earlier Tkinter application lives in [Source/](Source/). Desktop builds are listed in [Releases](https://github.com/AkvilaAkiel/Live-Stream-Analyzer/releases).
