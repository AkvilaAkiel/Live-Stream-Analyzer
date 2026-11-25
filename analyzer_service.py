import signal
import subprocess
import re
import os
import json
import time
import threading
import queue
from collections import deque
from datetime import datetime, timezone
import shutil
import urllib.request
from urllib.parse import urljoin, urlsplit
import mimetypes
import uuid
import html

# --- КОНФІГУРАЦІЯ ШЛЯХІВ ТА ЗМІННИХ ОТОЧЕННЯ ---
SCREENSHOT_DIR = os.getenv("SCREENSHOT_DIR", "/app/screenshots")
CONFIG_PATH = os.getenv("THRESHOLDS_PATH", "/app/config/config.json")
STREAMS_PATH = os.getenv("STREAMS_PATH", "/app/config/streams.json")
FFMPEG_BIN = shutil.which("ffmpeg") or "ffmpeg"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

# --- GITHUB SYNC (Опціонально) ---
GITHUB_RAW_URL = os.getenv("GITHUB_RAW_URL", "").strip()
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()

DEFAULT_THRESHOLDS = {
    "black_with_silence_sec": 2.5,
    "black_with_audio_sec": 6.0,
    "freeze_with_audio_sec": 8.0,
    "freeze_with_silence_sec": 5.0,
    "silence_threshold": 12,
    "vart_window": 4.0,
    "vart_min_count": 12,
    "alert_cooldown_sec": 300,
    "abr_profile_mode": "balanced"
}

BLACKDETECT_PIC_TH = 0.98
BLACKDETECT_PIX_TH = 0.10
FREEZEDETECT_NOISE = 0.006
FREEZEDETECT_MIN_D = 0.5


# --- ЧЕРГА ПОВІДОМЛЕНЬ TELEGRAM (ЗАХИСТ ВІД 429 TOO MANY REQUESTS) ---
tg_queue = queue.Queue()

def telegram_worker():
    """Фоновий потік, який гарантує відправку без перевищення лімітів (1 повідомлення на 1.5с)"""
    while True:
        task = tg_queue.get()
        if task is None:
            break
        caption, photo_path = task
        try:
            if photo_path and os.path.exists(photo_path):
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
                boundary = uuid.uuid4().hex
                body = bytearray()

                def add_field(name, value):
                    body.extend(f'--{boundary}\r\n'.encode('utf-8'))
                    body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode('utf-8'))
                    body.extend(f'{value}\r\n'.encode('utf-8'))

                add_field('chat_id', TELEGRAM_CHAT_ID)
                add_field('caption', caption)
                add_field('parse_mode', 'HTML')

                filename = os.path.basename(photo_path)
                mime_type = mimetypes.guess_type(photo_path)[0] or 'image/jpeg'

                body.extend(f'--{boundary}\r\n'.encode('utf-8'))
                body.extend(f'Content-Disposition: form-data; name="photo"; filename="{filename}"\r\n'.encode('utf-8'))
                body.extend(f'Content-Type: {mime_type}\r\n\r\n'.encode('utf-8'))
                with open(photo_path, 'rb') as f:
                    body.extend(f.read())
                body.extend(b'\r\n')
                body.extend(f'--{boundary}--\r\n'.encode('utf-8'))

                req = urllib.request.Request(url, data=body, headers={'Content-Type': f'multipart/form-data; boundary={boundary}'})
            else:
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                payload = json.dumps({"chat_id": TELEGRAM_CHAT_ID, "text": caption, "parse_mode": "HTML"}).encode('utf-8')
                req = urllib.request.Request(url, data=payload, headers={'Content-Type': 'application/json'})

            with urllib.request.urlopen(req, timeout=8):
                pass
        except Exception as e:
            print(f"⚠️ Помилка відправки Telegram: {type(e).__name__}", flush=True)

        time.sleep(1.5)
        tg_queue.task_done()

if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
    threading.Thread(target=telegram_worker, daemon=True).start()

def send_telegram_alert(caption, photo_path=None):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    tg_queue.put((caption, photo_path))


def cleanup_old_screenshots(days_to_keep=7):
    if not os.path.exists(SCREENSHOT_DIR):
        return
    now = time.time()
    for root, dirs, files in os.walk(SCREENSHOT_DIR):
        for f in files:
            path = os.path.join(root, f)
            if os.path.isfile(path) and os.stat(path).st_mtime < now - (days_to_keep * 86400):
                try: os.remove(path)
                except Exception: pass
        for d in dirs:
            dir_path = os.path.join(root, d)
            if not os.listdir(dir_path):
                try: os.rmdir(dir_path)
                except Exception: pass


def load_thresholds_config():
    if not os.path.exists(CONFIG_PATH):
        return {"defaults": DEFAULT_THRESHOLDS.copy(), "channels": {}}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        cfg.setdefault("defaults", {})
        for k, v in DEFAULT_THRESHOLDS.items():
            cfg["defaults"].setdefault(k, v)
        return cfg
    except Exception:
        return {"defaults": DEFAULT_THRESHOLDS.copy(), "channels": {}}


def resolve_thresholds(cfg, channel_name):
    res = dict(cfg.get("defaults", DEFAULT_THRESHOLDS))
    override = cfg.get("channels", {}).get(channel_name, {})
    if isinstance(override, dict):
        res.update(override)
    return res


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def sync_github_streams():
    if not GITHUB_RAW_URL:
        return
    try:
        headers = {'User-Agent': 'Stream-Analyzer/4.2'}
        if GITHUB_TOKEN:
            source = urlsplit(GITHUB_RAW_URL)
            if source.scheme != "https" or source.netloc != "raw.githubusercontent.com":
                print("⚠️ [GITHUB SYNC] Токен дозволено лише для raw.githubusercontent.com.", flush=True)
                return
            headers['Authorization'] = f'token {GITHUB_TOKEN}'

        req = urllib.request.Request(GITHUB_RAW_URL, headers=headers)
        opener = urllib.request.build_opener(NoRedirect())
        with opener.open(req, timeout=10) as response:
            content = response.read().decode('utf-8')

        data = json.loads(content)

        if not isinstance(data.get("channels"), list) or len(data["channels"]) == 0:
            print("⚠️ [GITHUB SYNC] Отримано порожній список каналів, оновлення відхилено.", flush=True)
            return

        if os.path.exists(STREAMS_PATH):
            with open(STREAMS_PATH, "r", encoding="utf-8") as f:
                current_content = f.read()
            if content == current_content:
                return

        with open(STREAMS_PATH, "w", encoding="utf-8") as f:
            f.write(content)
        print("✅ [GITHUB SYNC] streams.json успішно оновлено з репозиторію.", flush=True)
    except Exception as e:
        print(f"⚠️ [GITHUB SYNC] Не вдалося оновити streams.json: {type(e).__name__}", flush=True)


def resolve_abr_profile(url, mode="balanced", timeout=5):
    if not url.startswith(('http://', 'https://')): return url, None
    clean_url = url.split('?')[0].lower()
    if not clean_url.endswith(('.m3u8', '.m3u')): return url, None

    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Stream-Analyzer/4.2)'})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            content = response.read(65536).decode('utf-8', errors='replace')

        if '#EXT-X-STREAM-INF' not in content: return url, None
        if '#EXT-X-MEDIA:TYPE=AUDIO' in content: return url, "Master Playlist (Розділене аудіо)"

        lines = content.splitlines()
        variants = []
        for i, line in enumerate(lines):
            line = line.strip()
            if line.startswith('#EXT-X-STREAM-INF:'):
                bw = re.search(r'BANDWIDTH=(\d+)', line)
                bandwidth = int(bw.group(1)) if bw else float('inf')
                res_match = re.search(r'RESOLUTION=(\d+x\d+)', line)
                resolution = res_match.group(1) if res_match else None
                has_video = (resolution is not None) or any(c in line.lower() for c in ['avc', 'hvc', 'mp4v'])

                for j in range(i + 1, len(lines)):
                    if lines[j].strip() and not lines[j].startswith('#'):
                        variants.append({
                            'bandwidth': bandwidth, 'resolution': resolution,
                            'has_video': has_video, 'url': urljoin(url, lines[j].strip())
                        })
                        break

        if variants:
            video_variants = [v for v in variants if v['has_video']]
            pool = video_variants if video_variants else variants
            pool.sort(key=lambda x: x['bandwidth'])

            if mode == "highest": best = pool[-1]
            elif mode == "lowest": best = pool[0]
            else: best = pool[len(pool) // 2 if len(pool) > 2 else 0]
            return best['url'], f"{best['resolution'] or 'Video'} @ {best['bandwidth'] // 1000} kbps"
    except Exception:
        pass
    return url, None


class HeadlessStreamMonitor(threading.Thread):
    def __init__(self, channel_name, cdn_id, url, audio_enabled=True, thresholds=None):
        super().__init__()
        self.channel_name = channel_name
        self.cdn_id = cdn_id
        self.url = url
        self.effective_url = url
        self.audio_enabled = audio_enabled
        self.thresholds = thresholds or DEFAULT_THRESHOLDS.copy()
        self.process = None
        self.stop_event = threading.Event()
        self.last_alert_time = {}
        self.alerted_errors = set()

        self.consecutive_fails = 0
        self.source_down_alerted = False
        self.frame_ram_path = f"/dev/shm/frame_{uuid.uuid4().hex[:8]}.jpg"
        self.start_time = 0

    def redact(self, text):
        for url in (self.effective_url, self.url):
            if url:
                text = text.replace(url, "[redacted]")
        return re.sub(r'(?i)[a-z][a-z0-9+.-]*://[^\s<>"]+', "[redacted]", text)

    def log(self, event_type, msg):
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        print(f"[{now}] [{event_type}] [{self.channel_name} | {self.cdn_id}] {self.redact(msg)}", flush=True)

    def run(self):
        while not self.stop_event.is_set():
            try:
                self.monitor_loop()
            except Exception as e:
                self.log("ERROR", f"CRASH у моніторі: {e}")

            if not self.stop_event.is_set():
                self.consecutive_fails += 1
                backoff = min(180, 10 * self.consecutive_fails)
                self.log("WARNING", f"Відновлення каналу через {backoff} секунд...")

                if self.consecutive_fails >= 3 and not self.source_down_alerted:
                    self.trigger_alert("SOURCE_DOWN", "SOURCE DOWN (Джерело недоступне)")
                    self.source_down_alerted = True
                    self.alerted_errors.add("SOURCE_DOWN")

                time.sleep(backoff)

    def monitor_loop(self):
        target_url, profile_info = resolve_abr_profile(self.url, self.thresholds.get("abr_profile_mode", "balanced"))
        self.effective_url = target_url

        if profile_info: self.log("INFO", f"Виявлено ABR. Обрано: {profile_info}")
        else: self.log("INFO", "Запуск джерела")

        b_corr = self.thresholds.get("black_with_silence_sec", 2.5)
        b_solo = self.thresholds.get("black_with_audio_sec", 6.0)
        black_filter_d = max(0.3, min(b_corr, b_solo) / 3)

        command = [FFMPEG_BIN, '-y', '-nostdin', '-hide_banner', '-stats_period', '5']

        if self.effective_url.startswith(('http://', 'https://')):
            command.extend(['-reconnect', '1', '-reconnect_streamed', '1', '-reconnect_delay_max', '5', '-rw_timeout', '10000000'])

        if os.path.isfile(self.effective_url):
            command.extend(['-re', '-stream_loop', '-1'])

        command.extend([
            '-i', self.effective_url,
            '-filter_complex', (
                f'[0:v]fps=5,'
                f'blackdetect=d={black_filter_d:.2f}:pic_th={BLACKDETECT_PIC_TH}:pix_th={BLACKDETECT_PIX_TH},'
                f'freezedetect=d={FREEZEDETECT_MIN_D}:n={FREEZEDETECT_NOISE},'
                f'split[v_anal][v_shot_raw];'
                f'[v_shot_raw]format=yuvj420p[v_shot]'
            ),
            '-map', '[v_anal]', '-f', 'null', '-',
            '-map', '[v_shot]', '-strict', '-1', '-update', '1', '-r', '1', '-q:v', '3', self.frame_ram_path
        ])

        if self.audio_enabled:
            command.extend(['-map', '0:a:0?', '-af', f'silencedetect=noise=-50dB:d={self.thresholds["silence_threshold"]}', '-f', 'null', '-'])

        re_black = re.compile(r'black_start:\s*(-?[\d.]+)\s+black_end:\s*(-?[\d.]+)\s+black_duration:\s*(-?[\d.]+)')
        re_freeze_start = re.compile(r'freeze_start:\s*([\d.]+)')
        re_freeze_end = re.compile(r'freeze_end:\s*([\d.]+)')
        re_silence_start = re.compile(r'silence_start:\s*(-?[\d.]+)')
        re_silence_end = re.compile(r'silence_end:\s*(-?[\d.]+)')
        re_decoder_err = re.compile(r'(?i)(cabac|corrupt macroblock|error while decoding|concealing|error concealment|packet corrupt|pes packet size|pocs unavailable)')

        self.process = subprocess.Popen(command, stderr=subprocess.PIPE, universal_newlines=True, encoding='utf-8', errors='replace')
        out_queue = queue.Queue()
        def reader_thread(pipe, q):
            for line in iter(pipe.readline, ''): q.put(line)
            pipe.close()

        threading.Thread(target=reader_thread, args=(self.process.stderr, out_queue), daemon=True).start()

        state = {'is_frozen': False, 'freeze_start_ts': None, 'audio_silent': False, 'silence_intervals': [], 'active_errors': set(), 'vart_events': []}
        last_log_time = time.time()
        recent_stderr = deque(maxlen=8)
        ffmpeg_history = deque(maxlen=100)
        frames_decoded = 0
        self.start_time = time.time()

        while not self.stop_event.is_set():
            try:
                line = out_queue.get(timeout=0.2)
                last_log_time = time.time()
            except queue.Empty:
                line = None

            current_time = time.time()

            if current_time - last_log_time > 45:
                self.log("ERROR", "WATCHDOG: Немає даних від джерела 45с. Примусовий перезапуск.")
                self.process.kill()
                self.last_ffmpeg_err = "Watchdog Timeout 45s (Мертвий TCP сокет)"
                break

            if line:
                stripped = self.redact(line.strip())
                if stripped:
                    utc_ms = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]
                    ffmpeg_history.append(f"[{utc_ms} UTC] {stripped}")

                if stripped.startswith('frame='):
                    frames_decoded += 1
                    if frames_decoded >= 2:
                        self.consecutive_fails = 0
                        if self.source_down_alerted:
                            self.log("RESOLVED", "Відновлено: SOURCE DOWN")
                            self.trigger_recovery("SOURCE_DOWN")
                            self.source_down_alerted = False
                elif stripped:
                    recent_stderr.append(stripped)

                if re_decoder_err.search(line) and (current_time - self.start_time > 6.0):
                    utc_ms = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]
                    state['vart_events'].append((current_time, f"[{utc_ms} UTC] {stripped}", stripped))

                if re_freeze_start.search(line):
                    if not state['is_frozen']:
                        state['is_frozen'] = True
                        state['freeze_start_ts'] = current_time
                elif re_freeze_end.search(line):
                    state['is_frozen'] = False
                    state['freeze_start_ts'] = None

                if re_silence_start.search(line):
                    ts = float(re_silence_start.search(line).group(1))
                    state['audio_silent'] = True
                    state['silence_intervals'].append([ts, None])
                elif re_silence_end.search(line):
                    ts = float(re_silence_end.search(line).group(1))
                    state['audio_silent'] = False
                    if state['silence_intervals']: state['silence_intervals'][-1][1] = ts

                state['silence_intervals'] = state['silence_intervals'][-20:]
                m_black = re_black.search(line)
                if m_black:
                    b_dur = float(m_black.group(3))
                    overlapped = any(iv_start <= float(m_black.group(2)) and (iv_end is None or iv_end >= float(m_black.group(1))) for iv_start, iv_end in state['silence_intervals'])
                    needed = self.thresholds["black_with_silence_sec"] if overlapped else self.thresholds["black_with_audio_sec"]
                    if b_dur >= needed:
                        msg = f"BLACK ({b_dur:.1f}с, {'без звуку' if overlapped else 'зі звуком'})"
                        self.log("ERROR" if overlapped else "WARNING", msg)
                        if overlapped: self.trigger_alert("BLACK", msg, context_lines=list(ffmpeg_history))

            state['vart_events'] = [ev for ev in state['vart_events'] if current_time - ev[0] <= self.thresholds["vart_window"]]
            current_problems = set()
            problem_details = {}

            if state['is_frozen'] and state['freeze_start_ts']:
                elapsed = current_time - state['freeze_start_ts']
                needed = self.thresholds["freeze_with_silence_sec"] if state['audio_silent'] else self.thresholds["freeze_with_audio_sec"]
                if elapsed >= needed:
                    current_problems.add("FREEZE")
                    mode = "без звуку" if state['audio_silent'] else "зі звуком"
                    problem_details["FREEZE"] = f"FREEZE ({elapsed:.1f}с, {mode})"

            if len(state['vart_events']) >= float(self.thresholds["vart_min_count"]):
                current_problems.add("V-ART")
                problem_details["V-ART"] = f"V-ART ({len(state['vart_events'])} артефактів за {self.thresholds['vart_window']}с)"

            if state['audio_silent']:
                current_problems.add("MUTE")
                problem_details["MUTE"] = "MUTE (Повна тиша на доріжці)"

            new_errors = current_problems - state['active_errors']
            for err in new_errors:
                msg = problem_details.get(err, err)
                severity = "ERROR" if "без звуку" in msg or "MUTE" in msg or "V-ART" in msg else "WARNING"
                self.log(severity, msg)
                if severity == "ERROR":
                    matched_lines = list(state['vart_events']) if err == "V-ART" else []
                    self.trigger_alert(err, msg, matched_events=matched_lines, context_lines=list(ffmpeg_history))

            resolved_errors = state['active_errors'] - current_problems
            for err in resolved_errors:
                self.log("RESOLVED", f"Відновлено: {err}")
                if err in self.alerted_errors:
                    self.alerted_errors.discard(err)

            state['active_errors'] = current_problems

            if self.process.poll() is not None: break

        rc = self.process.wait()
        while not out_queue.empty():
            extra = self.redact(out_queue.get_nowait().strip())
            if extra and not extra.startswith('frame='): recent_stderr.append(extra)

        if not self.stop_event.is_set() and rc != 0:
            self.last_ffmpeg_err = " | ".join(list(recent_stderr)[-2:]) if recent_stderr else "невідома помилка"
            self.log("ERROR", f"FFmpeg зупинився (код {rc}): {self.last_ffmpeg_err}")

    def trigger_alert(self, err_key, details, matched_events=None, context_lines=None):
        now = time.time()
        if now - self.last_alert_time.get(err_key, 0) < self.thresholds.get("alert_cooldown_sec", 300):
            return

        self.last_alert_time[err_key] = now
        self.alerted_errors.add(err_key)

        safe_name = "".join(c for c in self.channel_name if c.isalnum() or c in ' _-').rstrip()
        target_dir = os.path.join(SCREENSHOT_DIR, datetime.now().strftime("%Y-%m-%d"), safe_name)
        os.makedirs(target_dir, exist_ok=True)
        ts_file = datetime.now().strftime('%H-%M-%S')
        base_path = os.path.join(target_dir, f"{err_key}_{ts_file}")

        saved_img_path = None
        if os.path.exists(self.frame_ram_path) and "MUTE" not in err_key and "SOURCE" not in err_key:
            saved_img_path = f"{base_path}.jpg"
            try: shutil.copy2(self.frame_ram_path, saved_img_path)
            except Exception: saved_img_path = None

        if matched_events or context_lines:
            saved_log_path = f"{base_path}.log"
            now_utc_full = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            try:
                with open(saved_log_path, "w", encoding="utf-8") as lf:
                    lf.write("=" * 72 + "\nINCIDENT DIAGNOSTIC REPORT\n" + "=" * 72 + "\n")
                    lf.write(f"Channel   : {self.channel_name}\nCDN ID    : {self.cdn_id}\nAlert Type: {err_key}\nDetails   : {details}\nTime (UTC): {now_utc_full}\n" + "=" * 72 + "\n\n")
                    if matched_events:
                        lf.write(f"--- ЗАФІКСОВАНІ АРТЕФАКТИ ДЕКОДЕРА ({len(matched_events)} шт.) ---\n")
                        for _, fmt_line, _ in matched_events: lf.write(fmt_line + "\n")
                        lf.write("\n")
                    if context_lines:
                        lf.write(f"--- КОНТЕКСТ FFMPEG ПЕРЕД ІНЦИДЕНТОМ (останні {len(context_lines)} рядків) ---\n")
                        for ctx_line in context_lines: lf.write(ctx_line + "\n")
            except Exception as e: self.log("WARNING", f"Не вдалося зберегти лог інциденту: {e}")

        safe_ch = html.escape(self.channel_name)
        now_utc = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")

        alert_text = (
            f"🚨 <b>АВАРІЯ НА КАНАЛІ</b>\n"
            f"📺 <b>Канал:</b> {safe_ch} (<code>{html.escape(self.cdn_id)}</code>)\n"
            f"⚠️ <b>Проблема:</b> {html.escape(self.redact(details))}\n"
            f"🕒 <b>Час:</b> {now_utc}"
        )

        # Захист від перевищення ліміту Telegram caption (1024 символи) при відправці фото
        if saved_img_path and len(alert_text) > 1000:
            alert_text = (
                f"🚨 <b>АВАРІЯ НА КАНАЛІ</b>\n"
                f"📺 <b>Канал:</b> {safe_ch} (<code>{html.escape(self.cdn_id)}</code>)\n"
                f"⚠️ <b>Проблема:</b> {html.escape(self.redact(details)[:250])}\n"
                f"🕒 <b>Час:</b> {now_utc}"
            )

        send_telegram_alert(alert_text, saved_img_path)

    def trigger_recovery(self, err_key):
        if err_key not in self.alerted_errors:
            return
        self.alerted_errors.discard(err_key)

        safe_ch = html.escape(self.channel_name)
        now_utc = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
        msg = (
            f"✅ <b>ВІДНОВЛЕНО СТАБІЛЬНИЙ ПОТІК</b>\n"
            f"📺 <b>Канал:</b> {safe_ch} (<code>{html.escape(self.cdn_id)}</code>)\n"
            f"ℹ️ <b>Статус:</b> Завершилась аварія <code>{html.escape(err_key)}</code>\n"
            f"🕒 <b>Час:</b> {now_utc}"
        )
        send_telegram_alert(msg, None)

    def stop(self):
        self.stop_event.set()
        if self.process:
            try: self.process.terminate()
            except Exception: pass
        if os.path.exists(self.frame_ram_path):
            try: os.remove(self.frame_ram_path)
            except Exception: pass


def sync_streams(active_monitors, thresholds_cfg):
    if not os.path.exists(STREAMS_PATH): return
    try:
        with open(STREAMS_PATH, "r", encoding="utf-8") as f: streams_data = json.load(f)
    except Exception as e:
        print(f"⚠️ [HOT-RELOAD] Помилка читання streams.json: {e}", flush=True)
        return

    desired_channels = {ch["name"]: ch for ch in streams_data.get("channels", []) if isinstance(ch, dict) and ch.get("name") and ch.get("url") and ch.get("enabled", True)}

    for name in list(active_monitors.keys()):
        if name not in desired_channels:
            print(f"🛑 [HOT-RELOAD] Видалення каналу: {name}", flush=True)
            active_monitors[name].stop()
            del active_monitors[name]

    for name, info in desired_channels.items():
        new_url = info["url"]
        new_cdn = info.get("cdn_id", "N/A")
        new_audio = info.get("audio", True)
        new_thresh = resolve_thresholds(thresholds_cfg, name)

        if name in active_monitors:
            curr = active_monitors[name]
            if curr.url != new_url or curr.cdn_id != new_cdn or curr.audio_enabled != new_audio or curr.thresholds != new_thresh:
                print(f"🔄 [HOT-RELOAD] Оновлення параметрів для '{name}'", flush=True)
                curr.stop()
                active_monitors[name] = HeadlessStreamMonitor(name, new_cdn, new_url, new_audio, new_thresh)
                active_monitors[name].daemon = True
                active_monitors[name].start()
        else:
            print(f"➕ [HOT-RELOAD] Старт нового каналу: '{name}'", flush=True)
            active_monitors[name] = HeadlessStreamMonitor(name, new_cdn, new_url, new_audio, new_thresh)
            active_monitors[name].daemon = True
            active_monitors[name].start()


def main():
    print("🚀 Stream Analyzer (24/7 Watchdog + Forensics + Telegram Queue + Fixes) запущено.", flush=True)
    active_monitors = {}
    last_streams_mtime, last_config_mtime = 0, 0
    last_cleanup, last_github_sync = 0, 0
    thresholds_cfg = load_thresholds_config()

    try:
        while True:
            now = time.time()
            if now - last_cleanup > 86400:
                cleanup_old_screenshots(days_to_keep=7)
                last_cleanup = now

            if now - last_github_sync > 60:
                sync_github_streams()
                last_github_sync = now

            if os.path.exists(CONFIG_PATH):
                cfg_mtime = os.path.getmtime(CONFIG_PATH)
                if cfg_mtime != last_config_mtime:
                    last_config_mtime = cfg_mtime
                    thresholds_cfg = load_thresholds_config()
                    sync_streams(active_monitors, thresholds_cfg)

            if os.path.exists(STREAMS_PATH):
                streams_mtime = os.path.getmtime(STREAMS_PATH)
                if streams_mtime != last_streams_mtime:
                    last_streams_mtime = streams_mtime
                    sync_streams(active_monitors, thresholds_cfg)

            time.sleep(3)

    except KeyboardInterrupt:
        print("\n🛑 Завершення роботи...", flush=True)
        for m in active_monitors.values(): m.stop()
        tg_queue.put(None)

def handle_sigterm(signum, frame):
    raise KeyboardInterrupt


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, handle_sigterm)
    main()
