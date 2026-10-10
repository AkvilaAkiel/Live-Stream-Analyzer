# Real-Time Live Stream Analyzer 📡🎥

> **Note:** *This is a standalone, open-source version of an internal tool developed to assist NOC and QA teams in autonomous tracking of OTT live streams.*

## 📌 Overview
Monitoring hundreds of live sports broadcasts simultaneously makes it impossible for human operators to catch every micro-freeze, artifact, or audio drop. 

**The Problem:** Traditional monitoring tools either require heavy enterprise infrastructure or generate too many false positives (e.g., flagging a naturally black scene in a movie as a stream failure).
**The Solution:** A lightweight, cross-platform desktop application built with Python and FFmpeg. It acts as an autonomous "Co-Pilot" for live broadcast engineers, utilizing heuristic logic to correlate video and audio anomalies in real-time, drastically reducing false positives and classifying issues by severity.

## 🚀 Business Impact
* **Reduced MTTR (Mean Time To Recovery):** Instant alerts allow operators to switch to backup sources seconds after a drop occurs.
* **Incident Documentation:** Automatically captures and categorizes timestamped screenshots of stream errors for post-incident reporting to B2B partners.
* **Operational Efficiency:** Allows a single engineer to confidently monitor 20+ streams concurrently.

## ✨ Key Features
* **🧠 Heuristic Event Correlation:** Differentiates between minor glitches and critical failures. For example, a 2-second video freeze is a *Warning* (Yellow), but a 2-second freeze correlated with audio silence is flagged as a *Critical Error* (Red).
* **⚡ Multi-threaded Architecture:** Runs intensive FFmpeg stream analysis in non-blocking background threads, communicating with the UI via thread-safe queues.
* **📸 Automated Snapshotting:** Upon detecting a critical error (e.g., V-ART artifact, Macroblock corruption, Freeze), the app automatically extracts a frame and saves it to a dynamically generated folder (`/alert_screenshots/date/channel/`).
* **🎛 Dynamic Thresholds:** Sensitivity for `blackdetect`, `freezedetect`, and `silencedetect` can be adjusted on the fly via `config.json` per channel.
* **💻 Cross-Platform GUI:** Built with `tkinter`, featuring custom dark/light themes (Dracula, Nord, Monokai, etc.) and optimized DPI awareness for Windows, macOS, and Linux.

## 🛠 Tech Stack
* **Language:** Python 3.x
* **Core Engine:** FFmpeg (Subprocess interaction)
* **Concurrency:** `threading`, `queue`
* **GUI:** `tkinter` (Native Desktop App)
* **Data Parsing:** Regex (Real-time stderr log parsing)

## 📋 How It Works
1. The user inputs a stream URL (HLS, SRT, RTMP, etc.) and a channel identifier.
2. The app spins up a headless FFmpeg instance in a background thread, applying specific video/audio filters.
3. A dedicated reader thread parses FFmpeg's `stderr` output in real-time using compiled regex patterns.
4. The logic engine calculates durations and correlates events (e.g., overlapping silence and black frames).
5. State changes are pushed to a Queue, which the main GUI thread consumes every 200ms to update the dashboard, trigger sounds, and save snapshots.

📥 Download Ready-to-Use App

Don't want to mess with Python dependencies? Download the compiled, ready-to-run executable for your OS from the Releases page:

🪟 Windows (.zip)

🍎 macOS (.zip)

🐧 Linux (.zip)
(Note: FFmpeg must still be installed on your system).

## ⚙️ Setup Instructions
1. Ensure [FFmpeg](https://ffmpeg.org/download.html) is installed and added to your system's PATH.
2. Clone the repository.
3. Run `python LiveStreamAnalyzer.py`.
4. Add a stream URL and start monitoring!
