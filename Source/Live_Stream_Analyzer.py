import sys
import subprocess
import re
import os
import json
import time
import threading
import queue
from datetime import datetime, timezone
import tkinter as tk
from tkinter import ttk, messagebox
import platform
import shutil

# --- ФІКС ДЛЯ РОЗМИТИХ ШРИФТІВ НА WINDOWS ---
if platform.system() == "Windows":
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            windll.user32.SetProcessDPIAware()
        except Exception:
            pass

if sys.platform == "win32":
    CREATE_NO_WINDOW = 0x08000000
else:
    CREATE_NO_WINDOW = 0

if getattr(sys, 'frozen', False):
    SCRIPT_DIR = os.path.dirname(sys.executable)
    if sys.platform == "darwin" and ".app/Contents/MacOS" in sys.executable:
        SCRIPT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, "../../.."))
else:
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

SCREENSHOT_DIR = os.path.join(SCRIPT_DIR, "alert_screenshots")

FFMPEG_BIN = 'ffmpeg'
if sys.platform == "darwin" and not shutil.which('ffmpeg'):
    if os.path.exists('/opt/homebrew/bin/ffmpeg'): FFMPEG_BIN = '/opt/homebrew/bin/ffmpeg'
    elif os.path.exists('/usr/local/bin/ffmpeg'): FFMPEG_BIN = '/usr/local/bin/ffmpeg'

# --- НАЛАШТУВАННЯ АНАЛІЗАТОРА (fallback, якщо немає config.json) ---
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")

DEFAULT_THRESHOLDS = {
    "black_correlated_sec": 1.5,
    "black_solo_sec": 5.0,
    "freeze_correlated_sec": 2.0,
    "freeze_solo_sec": 6.0,
    "silence_threshold": 5,
    "vart_window": 3.0,
    "vart_min_count": 2
}

BLACKDETECT_PIC_TH = 0.98
BLACKDETECT_PIX_TH = 0.10
FREEZEDETECT_NOISE = 0.006
FREEZEDETECT_MIN_D = 0.5


def load_config():
    if not os.path.exists(CONFIG_PATH):
        default_cfg = {"defaults": DEFAULT_THRESHOLDS.copy(), "channels": {}}
        save_config(default_cfg)
        return default_cfg
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"⚠️ Помилка читання config.json ({e}), використовую дефолти.")
        return {"defaults": DEFAULT_THRESHOLDS.copy(), "channels": {}}

    cfg.setdefault("defaults", {})
    cfg.setdefault("channels", {})
    for key, val in DEFAULT_THRESHOLDS.items():
        cfg["defaults"].setdefault(key, val)
    return cfg


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except OSError as e:
        print(f"⚠️ Не вдалося зберегти config.json: {e}")


def resolve_thresholds(cfg, channel_name):
    result = dict(cfg.get("defaults", DEFAULT_THRESHOLDS))
    override = cfg.get("channels", {}).get(channel_name, {})
    result.update(override)
    return result

THEMES = {
    "Dracula (Темна)": {
        "bg": "#282a36", "fg": "#f8f8f2",
        "log_bg": "#1e1f29", "log_fg": "#50fa7b", "log_err": "#ff5555",
        "log_warn": "#ffb86c",
        "tree_bg": "#282a36", "tree_fg": "#f8f8f2",
        "tag_ok_bg": "#234026", "tag_ok_fg": "#50fa7b",
        "tag_err_bg": "#4a2123", "tag_err_fg": "#ff5555",
        "tag_warn_bg": "#4a3c1e", "tag_warn_fg": "#ffb86c",
        "select_bg": "#44475a"
    },
    "Monokai (Темна)": {
        "bg": "#272822", "fg": "#f8f8f2",
        "log_bg": "#1e1e1e", "log_fg": "#a6e22e", "log_err": "#f92672",
        "log_warn": "#e6db74",
        "tree_bg": "#272822", "tree_fg": "#f8f8f2",
        "tag_ok_bg": "#304523", "tag_ok_fg": "#a6e22e",
        "tag_err_bg": "#5a2323", "tag_err_fg": "#f92672",
        "tag_warn_bg": "#5a4d1a", "tag_warn_fg": "#e6db74",
        "select_bg": "#49483e"
    },
    "Nord (Темна)": {
        "bg": "#2e3440", "fg": "#d8dee9",
        "log_bg": "#242933", "log_fg": "#a3be8c", "log_err": "#bf616a",
        "log_warn": "#ebcb8b",
        "tree_bg": "#2e3440", "tree_fg": "#d8dee9",
        "tag_ok_bg": "#354a46", "tag_ok_fg": "#a3be8c",
        "tag_err_bg": "#4c3740", "tag_err_fg": "#bf616a",
        "tag_warn_bg": "#4a4536", "tag_warn_fg": "#ebcb8b",
        "select_bg": "#434c5e"
    },
    "Gruvbox (Темна)": {
        "bg": "#282828", "fg": "#ebdbb2",
        "log_bg": "#1d2021", "log_fg": "#b8bb26", "log_err": "#cc241d",
        "log_warn": "#fabd2f",
        "tree_bg": "#282828", "tree_fg": "#ebdbb2",
        "tag_ok_bg": "#424726", "tag_ok_fg": "#b8bb26",
        "tag_err_bg": "#502224", "tag_err_fg": "#fb4934",
        "tag_warn_bg": "#50411a", "tag_warn_fg": "#fabd2f",
        "select_bg": "#504945"
    },
    "GitHub (Світла)": {
        "bg": "#ffffff", "fg": "#24292e",
        "log_bg": "#f6f8fa", "log_fg": "#22863a", "log_err": "#cb2431",
        "log_warn": "#b08800",
        "tree_bg": "#ffffff", "tree_fg": "#24292e",
        "tag_ok_bg": "#dcffe4", "tag_ok_fg": "#144620",
        "tag_err_bg": "#ffeef0", "tag_err_fg": "#9e1c23",
        "tag_warn_bg": "#fff8c5", "tag_warn_fg": "#b08800",
        "select_bg": "#0366d6"
    },
    "Solarized (Світла)": {
        "bg": "#fdf6e3", "fg": "#657b83",
        "log_bg": "#eee8d5", "log_fg": "#859900", "log_err": "#dc322f",
        "log_warn": "#b58900",
        "tree_bg": "#fdf6e3", "tree_fg": "#657b83",
        "tag_ok_bg": "#e9f2ce", "tag_ok_fg": "#5b7000",
        "tag_err_bg": "#f9e2e2", "tag_err_fg": "#992320",
        "tag_warn_bg": "#fdf6e3", "tag_warn_fg": "#b58900",
        "select_bg": "#eee8d5"
    }
}

class StreamMonitor(threading.Thread):
    def __init__(self, channel_name, cdn_id, url, event_queue, thresholds=None):
        super().__init__()
        self.channel_name = channel_name
        self.cdn_id = cdn_id
        self.url = url
        self.event_queue = event_queue
        self.thresholds = thresholds or DEFAULT_THRESHOLDS.copy()
        self.process = None
        self.stop_event = threading.Event()

    def run(self):
        self.send_event("INFO", f"Запуск моніторингу: {self.url}")

        command = [FFMPEG_BIN, '-hide_banner', '-nostats']

        if sys.platform == "win32":
            command.extend(['-hwaccel', 'none'])

        if self.url.startswith('http'):
            command.extend([
                '-reconnect', '1',
                '-reconnect_streamed', '1',
                '-reconnect_delay_max', '5',
                '-rw_timeout', '10000000',
                '-recv_buffer_size', '5000000'
            ])

        black_filter_d = max(0.3, min(self.thresholds["black_correlated_sec"], self.thresholds["black_solo_sec"]) / 3)
        command.extend([
            '-i', self.url,
            '-map', '0:v',
            '-map', '0:a:0?', '-map', '0:a:1?', '-map', '0:a:2?',
            '-vf', (
                f'fps=5,'
                f'blackdetect=d={black_filter_d:.2f}:pic_th={BLACKDETECT_PIC_TH}:pix_th={BLACKDETECT_PIX_TH},'
                f'freezedetect=d={FREEZEDETECT_MIN_D}:n={FREEZEDETECT_NOISE}'
            ),
            '-af', f'silencedetect=noise=-50dB:d={self.thresholds["silence_threshold"]}',
            '-threads', '0', '-f', 'null', '-'
        ])

        re_black = re.compile(r'black_start:\s*(-?[\d.]+)\s+black_end:\s*(-?[\d.]+)\s+black_duration:\s*(-?[\d.]+)')
        re_freeze_start = re.compile(r'freeze_start:\s*([\d.]+)')
        re_freeze_end = re.compile(r'freeze_end:\s*([\d.]+)')
        re_silence_start = re.compile(r'silence_start:\s*(-?[\d.]+)')
        re_silence_end = re.compile(r'silence_end:\s*(-?[\d.]+)')
        re_silence_track = re.compile(r'silencedetect_(\d+)')
        re_decoder_err = re.compile(r'(?i)(cabac decode|corrupt macroblock|error while decoding MB|concealing|decode_slice_header|truncated packet|left block unavailable)')

        try:
            self.process = subprocess.Popen(
                command, stderr=subprocess.PIPE, universal_newlines=True,
                encoding='utf-8', errors='replace', creationflags=CREATE_NO_WINDOW
            )

            # --- НОВЕ: Фонове читання логів (щоб головний цикл не зависав) ---
            out_queue = queue.Queue()
            def reader_thread(pipe, q):
                for line in pipe:
                    q.put(line)

            reader = threading.Thread(target=reader_thread, args=(self.process.stderr, out_queue))
            reader.daemon = True
            reader.start()

            state = {
                'is_frozen': False, 'freeze_start_ts': None,
                'muted_tracks': set(),
                'silence_intervals': [],
                'active_errors': set(), 'vart_timestamps': []
            }

            # --- НОВЕ: Незалежний таймер замість блокуючого for line in ... ---
            while not self.stop_event.is_set():
                # Читаємо рядок, якщо він є. Якщо ні - чекаємо лише 0.2 секунди і йдемо далі!
                try:
                    line = out_queue.get(timeout=0.2)
                except queue.Empty:
                    line = None

                current_time = time.time()

                # Якщо рядок є - парсимо події
                if line:
                    print(f"📡 [FFMPEG | {self.channel_name}] {line.strip()}")

                    if re_decoder_err.search(line):
                        print(f"👾 [DEBUG V-ART | {self.channel_name}] Спіймав артефакт: {line.strip()}")
                        state['vart_timestamps'].append(current_time)

                    if re_freeze_start.search(line):
                        if not state['is_frozen']:
                            state['is_frozen'] = True
                            state['freeze_start_ts'] = current_time
                    elif re_freeze_end.search(line):
                        state['is_frozen'] = False
                        state['freeze_start_ts'] = None

                    if re_silence_start.search(line):
                        ts_match = re_silence_start.search(line)
                        ts = float(ts_match.group(1))
                        m_idx = re_silence_track.search(line)
                        track = m_idx.group(1) if m_idx else "?"
                        was_any_silent = bool(state['muted_tracks'])
                        state['muted_tracks'].add(track)
                        if not was_any_silent:
                            state['silence_intervals'].append([ts, None])
                    elif re_silence_end.search(line):
                        ts_match = re_silence_end.search(line)
                        ts = float(ts_match.group(1))
                        m_idx = re_silence_track.search(line)
                        track = m_idx.group(1) if m_idx else "?"
                        state['muted_tracks'].discard(track)
                        if not state['muted_tracks'] and state['silence_intervals']:
                            state['silence_intervals'][-1][1] = ts

                    state['silence_intervals'] = state['silence_intervals'][-20:]

                    m_black = re_black.search(line)
                    if m_black:
                        b_start, b_end, b_dur = (float(m_black.group(1)), float(m_black.group(2)), float(m_black.group(3)))
                        overlapped_silence = any(
                            iv_start <= b_end and (iv_end is None or iv_end >= b_start)
                            for iv_start, iv_end in state['silence_intervals']
                        )
                        needed = self.thresholds["black_correlated_sec"] if overlapped_silence else self.thresholds["black_solo_sec"]
                        if b_dur >= needed:
                            mode = "звук був тихий" if overlapped_silence else "звук йде"
                            severity = "ERROR" if overlapped_silence else "WARNING"
                            self.send_event(severity, f"BLACK ({b_dur:.1f}с, {mode})")
                            self.send_event("RESOLVED", "BLACK")

                # --- НЕЗАЛЕЖНА ВІД ЛОГІВ ПЕРЕВІРКА ТАЙМЕРІВ ---

                # Очищення старих артефактів
                state['vart_timestamps'] = [t for t in state['vart_timestamps'] if current_time - t <= self.thresholds["vart_window"]]

                current_problems = set()
                problem_details = {}

                audio_silent = bool(state['muted_tracks'])

                # Перевірка FREEZE (тепер спрацює РІВНО через 2.0 або 6.0 сек, навіть якщо FFmpeg мовчить!)
                if state['is_frozen'] and state['freeze_start_ts']:
                    freeze_elapsed = current_time - state['freeze_start_ts']
                    needed_f = self.thresholds["freeze_correlated_sec"] if not audio_silent else self.thresholds["freeze_solo_sec"]
                    if freeze_elapsed >= needed_f:
                        current_problems.add("FREEZE")
                        mode = "звук йде" if not audio_silent else "звук тихий"
                        problem_details["FREEZE"] = f"FREEZE ({freeze_elapsed:.1f}с, {mode})"

                if len(state['vart_timestamps']) >= self.thresholds["vart_min_count"]:
                    current_problems.add("V-ART")

                if state['muted_tracks']:
                    tracks = ",".join(sorted(state['muted_tracks']))
                    mute_key = f"MUTE (Track {tracks})" if tracks != "?" else "MUTE"
                    current_problems.add(mute_key)

                new_errors = current_problems - state['active_errors']
                for err in new_errors:
                    msg = problem_details.get(err, err)
                    severity = "WARNING" if "звук йде" in msg else "ERROR"
                    self.send_event(severity, msg)

                    if "MUTE" not in err:
                        threading.Thread(target=self.capture_screenshot, args=(err,)).start()

                resolved_errors = state['active_errors'] - current_problems
                for err in resolved_errors:
                    self.send_event("RESOLVED", err)

                state['active_errors'] = current_problems

                # Перевірка, чи не "вмер" сам FFmpeg
                if self.process.poll() is not None and not self.stop_event.is_set():
                    break

            self.process.wait()
            if not self.stop_event.is_set():
                self.send_event("ERROR", f"DROP (Code: {self.process.returncode})")

        except Exception as e:
            if not self.stop_event.is_set(): self.send_event("ERROR", f"CRASH: {e}")

    # ... capture_screenshot, send_event, stop залишаються без змін

    def capture_screenshot(self, error_type):
        safe_err = error_type.split()[0]
        today_us = datetime.now().strftime("%m.%d.%Y")
        safe_name = "".join([c for c in self.channel_name if c.isalnum() or c in ' _-']).rstrip()
        target_dir = os.path.join(SCREENSHOT_DIR, today_us, safe_name)
        os.makedirs(target_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%H-%M-%S")
        screenshot_path = os.path.join(target_dir, f"{safe_err}_{timestamp}.jpg")

        cmd = [FFMPEG_BIN, '-y', '-i', self.url, '-vframes', '1', '-q:v', '2', screenshot_path]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)

    def send_event(self, event_type, message):
        self.event_queue.put({'channel': self.channel_name, 'cdn_id': self.cdn_id, 'type': event_type, 'message': message})

    def stop(self):
        self.stop_event.set()
        if self.process: self.process.terminate()

class QAGodModeApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Stream Analyzer")
        self.root.geometry("1100x650")

        try:
            icon_path = os.path.join(SCRIPT_DIR, "icon.png")
            if os.path.exists(icon_path):
                img = tk.PhotoImage(file=icon_path)
                self.root.iconphoto(True, img)
        except Exception:
            pass

        self.style = ttk.Style()
        if sys.platform != "darwin": self.style.theme_use('clam')

        self.event_queue = queue.Queue()
        self.monitors = {}
        self.sound_enabled = True
        self.config = load_config()

        self.setup_ui()
        self.fix_cyrillic_hotkeys()

        if sys.platform != "darwin":
            self.apply_theme("Dracula (Темна)")

        self.process_queue()

    def fix_cyrillic_hotkeys(self):
        def handle_ctrl(event):
            if event.keysym.lower() in ['c', 'v', 'x', 'a']:
                return None

            if event.keycode == 67:
                event.widget.event_generate('<<Copy>>')
                return "break"
            elif event.keycode == 86:
                event.widget.event_generate('<<Paste>>')
                return "break"
            elif event.keycode == 88:
                event.widget.event_generate('<<Cut>>')
                return "break"
            elif event.keycode == 65:
                event.widget.event_generate('<<SelectAll>>')
                return "break"

        self.root.bind('<Control-KeyPress>', handle_ctrl)

    def setup_ui(self):
        frame_top = ttk.Frame(self.root, padding=10)
        frame_top.pack(fill=tk.X)

        ttk.Label(frame_top, text="Назва:").pack(side=tk.LEFT, padx=2)
        self.entry_name = ttk.Entry(frame_top, width=15)
        self.entry_name.pack(side=tk.LEFT, padx=2)

        ttk.Label(frame_top, text="CDN ID:").pack(side=tk.LEFT, padx=2)
        self.entry_cdn = ttk.Entry(frame_top, width=12)
        self.entry_cdn.pack(side=tk.LEFT, padx=2)

        ttk.Label(frame_top, text="URL:").pack(side=tk.LEFT, padx=2)
        self.entry_url = ttk.Entry(frame_top, width=35)
        self.entry_url.pack(side=tk.LEFT, padx=2)

        ttk.Button(frame_top, text="🛰️ Додати", command=self.add_stream).pack(side=tk.LEFT, padx=10)
        ttk.Button(frame_top, text="⚙️ Перезавант. конфіг", command=self.reload_config).pack(side=tk.LEFT, padx=2)

        if sys.platform != "darwin":
            ttk.Label(frame_top, text="🌓 Тема:").pack(side=tk.LEFT, padx=(10, 2))
            self.theme_var = tk.StringVar(value="Dracula (Темна)")
            self.theme_combo = ttk.Combobox(frame_top, textvariable=self.theme_var, values=list(THEMES.keys()), state="readonly", width=18)
            self.theme_combo.pack(side=tk.LEFT)
            self.theme_combo.bind("<<ComboboxSelected>>", lambda e: self.apply_theme(self.theme_var.get()))

        self.frame_mid = ttk.LabelFrame(self.root, text=" Активні моніторинги ", padding=10)
        self.frame_mid.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        columns = ("channel", "cdn", "status")
        self.tree = ttk.Treeview(self.frame_mid, columns=columns, show="headings", height=8, selectmode="browse")
        self.tree.heading("channel", text="Назва каналу")
        self.tree.heading("cdn", text="CDN ID")
        self.tree.heading("status", text="Поточний статус")
        self.tree.column("channel", width=200)
        self.tree.column("cdn", width=100)
        self.tree.column("status", width=550)
        self.tree.pack(fill=tk.BOTH, expand=True)

        self.tree.tag_configure('ok', background='#d4edda', foreground='black')
        self.tree.tag_configure('error', background='#f8d7da', foreground='black')
        self.tree.tag_configure('warning', background='#fff3cd', foreground='black')

        frame_controls = ttk.Frame(self.frame_mid, padding=(0, 5, 0, 0))
        frame_controls.pack(fill=tk.X)

        ttk.Button(frame_controls, text="🪄 Перезапустити обране", command=self.restart_selected).pack(side=tk.LEFT, padx=2)
        ttk.Button(frame_controls, text="🚫 Видалити обране", command=self.delete_selected).pack(side=tk.LEFT, padx=2)

        self.btn_sound = ttk.Button(frame_controls, text="🔔 Звук: УВІМК", command=self.toggle_sound)
        self.btn_sound.pack(side=tk.LEFT, padx=10)

        ttk.Button(frame_controls, text="👁️‍🗨️ Скриншоти", command=self.open_screenshot_folder).pack(side=tk.LEFT, padx=2)
        ttk.Button(frame_controls, text="🧨 Очистити все", command=self.clear_all).pack(side=tk.RIGHT, padx=2)

        self.frame_bottom = ttk.LabelFrame(self.root, text=" Журнал подій (Тільки проблеми) ", padding=10)
        self.frame_bottom.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        self.log_text = tk.Text(self.frame_bottom, state=tk.DISABLED, height=10, font=("Consolas", 10))
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self.log_text.tag_configure("error", foreground="#ff4444")
        self.log_text.tag_configure("warning", foreground="#ffc107")

    def reload_config(self):
        self.config = load_config()

        restarted = []
        for ch, monitor_data in list(self.monitors.items()):
            thresholds = resolve_thresholds(self.config, ch)
            monitor_data.stop()
            new_monitor = StreamMonitor(ch, monitor_data.cdn_id, monitor_data.url, self.event_queue, thresholds)
            new_monitor.daemon = True
            new_monitor.start()
            self.monitors[ch] = new_monitor
            self.tree.item(ch, values=(ch, monitor_data.cdn_id, "🟢 ОК (Конфіг оновлено)"), tags=('ok',))
            restarted.append(ch)

        if restarted:
            self.log("СИСТЕМА", "", f"⚙️ Конфіг перезавантажено. Оновлено пороги для: {', '.join(restarted)}")
        else:
            self.log("СИСТЕМА", "", "⚙️ Конфіг перезавантажено (активних каналів немає).")

    def toggle_sound(self):
        self.sound_enabled = not self.sound_enabled
        self.btn_sound.config(text="🔔 Звук: УВІМК" if self.sound_enabled else "🔕 Звук: ВИМК")

    def open_screenshot_folder(self):
        os.makedirs(SCREENSHOT_DIR, exist_ok=True)
        if sys.platform == "win32": os.startfile(SCREENSHOT_DIR)
        elif sys.platform == "darwin": subprocess.Popen(["open", SCREENSHOT_DIR])
        else: subprocess.Popen(["xdg-open", SCREENSHOT_DIR])

    def apply_theme(self, theme_name):
        if sys.platform == "darwin": return
        colors = THEMES[theme_name]
        self.root.configure(bg=colors["bg"])
        self.style.configure("TFrame", background=colors["bg"])
        self.style.configure("TLabel", background=colors["bg"], foreground=colors["fg"])
        self.style.configure("TLabelframe", background=colors["bg"], foreground=colors["fg"])
        self.style.configure("TLabelframe.Label", background=colors["bg"], foreground=colors["fg"])
        self.style.configure("Treeview", background=colors["tree_bg"], foreground=colors["tree_fg"], fieldbackground=colors["tree_bg"], rowheight=25)
        self.style.map('Treeview', background=[('selected', colors["select_bg"])])
        self.style.configure("Treeview.Heading", background=colors["bg"], foreground=colors["fg"])

        self.log_text.configure(bg=colors["log_bg"], fg=colors["log_fg"], insertbackground=colors["fg"])
        self.log_text.tag_configure("error", foreground=colors.get("log_err", "#ff4444"))
        self.log_text.tag_configure("warning", foreground=colors.get("log_warn", "#ffc107"))

        self.tree.tag_configure('ok', background=colors["tag_ok_bg"], foreground=colors["tag_ok_fg"])
        self.tree.tag_configure('error', background=colors["tag_err_bg"], foreground=colors["tag_err_fg"])
        if "tag_warn_bg" in colors:
            self.tree.tag_configure('warning', background=colors["tag_warn_bg"], foreground=colors["tag_warn_fg"])

    def get_selected_channel(self):
        selected = self.tree.selection()
        return selected[0] if selected else messagebox.showwarning("Увага", "Оберіть канал!")

    def restart_selected(self):
        ch = self.get_selected_channel()
        if ch and ch in self.monitors:
            monitor_data = self.monitors[ch]
            self.log(ch, monitor_data.cdn_id, "🔄 Перезапуск моніторингу...")
            monitor_data.stop()
            self.tree.item(ch, values=(ch, monitor_data.cdn_id, "🟢 ОК"), tags=('ok',))
            thresholds = resolve_thresholds(self.config, ch)
            new_monitor = StreamMonitor(ch, monitor_data.cdn_id, monitor_data.url, self.event_queue, thresholds)
            new_monitor.daemon = True
            new_monitor.start()
            self.monitors[ch] = new_monitor

    def delete_selected(self):
        ch = self.get_selected_channel()
        if ch and ch in self.monitors:
            self.monitors[ch].stop()
            del self.monitors[ch]
            self.tree.delete(ch)
            self.log(ch, "", "🗑️ Видалено зі списку.")

    def clear_all(self):
        if not self.monitors: return
        if messagebox.askyesno("Підтвердження", "Видалити всі канали з моніторингу?"):
            for ch, monitor in self.monitors.items():
                monitor.stop()
                self.tree.delete(ch)
            self.monitors.clear()
            self.log("СИСТЕМА", "", "🧹 Всі канали видалено.")

    def log(self, channel, cdn, message, tag=None):
        loc_time = datetime.now().strftime("%H:%M:%S")
        utc_time = datetime.now(timezone.utc).strftime("%H:%M:%S")
        cdn_str = f" [{cdn}]" if cdn else ""
        msg = f"[{loc_time} | UTC {utc_time}] [{channel}]{cdn_str} {message}\n"

        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, msg, tag)
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

    def add_stream(self):
        name, cdn, url = self.entry_name.get().strip(), self.entry_cdn.get().strip(), self.entry_url.get().strip()
        if not name or not url: return messagebox.showwarning("Помилка", "Введіть назву та URL!")
        if name in self.monitors: return messagebox.showinfo("Увага", "Канал вже моніториться!")

        self.tree.insert("", tk.END, iid=name, values=(name, cdn, "🟢 ОК (Аналіз...)"), tags=('ok',))
        thresholds = resolve_thresholds(self.config, name)
        monitor = StreamMonitor(name, cdn, url, self.event_queue, thresholds)
        monitor.daemon = True
        monitor.start()
        self.monitors[name] = monitor
        for entry in (self.entry_name, self.entry_cdn, self.entry_url): entry.delete(0, tk.END)

    def process_queue(self):
        try:
            while True:
                event = self.event_queue.get_nowait()
                ch, cdn, msg, t = event['channel'], event['cdn_id'], event['message'], event['type']
                if ch not in self.monitors and t != "INFO": continue

                if t == "ERROR":
                    self.log(ch, cdn, f"🚨 АВАРІЯ: {msg}", tag="error")
                    self.tree.item(ch, values=(ch, cdn, f"🔴 {msg}"), tags=('error',))
                    if self.sound_enabled: self.root.bell()
                elif t == "WARNING":
                    self.log(ch, cdn, f"⚠️ УВАГА: {msg}", tag="warning")
                    self.tree.item(ch, values=(ch, cdn, f"🟡 {msg}"), tags=('warning',))
                elif t == "RESOLVED":
                    self.log(ch, cdn, f"✅ ВІДНОВЛЕНО: {msg}")
                    self.tree.item(ch, values=(ch, cdn, "🟢 ОК"), tags=('ok',))
                elif t == "INFO":
                    self.log(ch, cdn, f"ℹ️ {msg}")
        except queue.Empty: pass
        finally: self.root.after(200, self.process_queue)

    def on_close(self):
        for monitor in self.monitors.values(): monitor.stop()
        self.root.destroy()

if __name__ == "__main__":
    root = tk.Tk()
    app = QAGodModeApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()
