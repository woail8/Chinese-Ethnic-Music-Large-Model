import os
import threading
import tkinter as tk
from dataclasses import dataclass
from tkinter import filedialog, messagebox, ttk
from typing import List, Optional, Tuple

import play_midi_with_piano_samples as engine
from mci_audio_player import MciWavePlayer


@dataclass(frozen=True)
class NoteEvent:
    start_s: float
    end_s: float
    note: int
    velocity: int
    channel: int

    @property
    def duration_s(self) -> float:
        return max(0.0, self.end_s - self.start_s)

    @property
    def note_name(self) -> str:
        return engine.midi_to_note_name(self.note)


def _note_events_from_midi(midi_path: str) -> Tuple[List[NoteEvent], float, int, int]:
    tpq, tempos, spans = engine.parse_midi(midi_path)
    tempo_points = engine.build_tempo_map(tpq, tempos)
    events: List[NoteEvent] = []
    max_end = 0.0
    in_range = 0
    out_range = 0
    for sp in spans:
        if sp.note < 21 or sp.note > 108:
            out_range += 1
            continue
        if sp.end_tick <= sp.start_tick:
            continue
        st = engine.tick_to_seconds(sp.start_tick, tpq, tempo_points)
        et = engine.tick_to_seconds(sp.end_tick, tpq, tempo_points)
        if et <= st:
            continue
        events.append(NoteEvent(st, et, sp.note, sp.velocity, sp.channel))
        in_range += 1
        if et > max_end:
            max_end = et
    events.sort(key=lambda e: (e.start_s, e.note, e.channel))
    return events, max_end, in_range, out_range


def _vel_color(vel: int) -> str:
    v = max(0, min(127, int(vel)))
    t = v / 127.0
    r = int(60 + 160 * t)
    g = int(120 + 80 * t)
    b = int(200 - 120 * t)
    return f"#{r:02x}{g:02x}{b:02x}"


def _fmt_ms(ms: int) -> str:
    ms = max(0, int(ms))
    s = ms // 1000
    m = s // 60
    ss = s % 60
    msec = ms % 1000
    return f"{m:02d}:{ss:02d}.{msec:03d}"


def _resolve_sample_leaf(dir_path: str) -> str:
    dir_path = os.path.abspath(dir_path)
    nested = os.path.join(dir_path, "钢琴88键独立音频文件")
    if os.path.isdir(nested):
        return nested
    return dir_path


def _is_valid_88key_sample_dir(dir_path: str) -> bool:
    leaf = _resolve_sample_leaf(dir_path)
    for midi in range(21, 109):
        p = os.path.join(leaf, f"{engine.midi_to_note_name(midi)}.wav")
        if not os.path.isfile(p):
            return False
    return True


def _scan_instruments(samples_root: str) -> List[Tuple[str, str]]:
    samples_root = os.path.abspath(samples_root)
    out: List[Tuple[str, str]] = []
    if not os.path.isdir(samples_root):
        return out

    try:
        entries = sorted(os.listdir(samples_root))
    except Exception:
        return out

    for name in entries:
        full = os.path.join(samples_root, name)
        if not os.path.isdir(full):
            continue
        if _is_valid_88key_sample_dir(full):
            label = "钢琴" if name == "钢琴88键独立音频文件" else name
            out.append((label, full))
    if out:
        return out

    if _is_valid_88key_sample_dir(samples_root):
        out.append((os.path.basename(samples_root) or "当前目录", samples_root))
    return out


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("MIDI 采样演奏可视化（钢琴88键）")
        self.root.geometry("1100x750")

        self.midi_path = tk.StringVar(value="")
        self.samples_dir = tk.StringVar(value=r"D:\民族文化大模型\模拟乐器演奏\音频文件")
        self.instrument = tk.StringVar(value="")
        self._instrument_options: List[Tuple[str, str]] = []
        self.out_wav = tk.StringVar(value="")
        self.status = tk.StringVar(value="就绪")
        self.hover = tk.StringVar(value="")

        self.events: List[NoteEvent] = []
        self.event_starts: List[float] = []
        self.total_seconds: float = 0.0
        self.rect_tags: List[str] = []
        self.selected_idx: Optional[int] = None
        self.roll_w = 1
        self.roll_h = 1
        self.px_per_sec = 140
        self.roll_left = 70
        self.roll_top = 20
        self.max_note = 108
        self.min_note = 21

        self.player = MciWavePlayer()
        self.play_wav_path = ""
        self.play_total_ms = 0
        self.play_pos_ms = tk.IntVar(value=0)
        self.play_pos_str = tk.StringVar(value="00:00.000 / 00:00.000")
        self.is_seeking = False
        self.follow_playhead = tk.BooleanVar(value=True)
        self.speed_percent = tk.IntVar(value=100)
        self.play_state = tk.StringVar(value="stopped")
        self.last_follow_idx: Optional[int] = None

        self._build_ui()
        self._schedule_tick()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._refresh_instruments()

    def _build_ui(self) -> None:
        top = ttk.Frame(self.root, padding=10)
        top.pack(fill=tk.X)

        ttk.Label(top, text="MIDI：").grid(row=0, column=0, sticky=tk.W)
        midi_entry = ttk.Entry(top, textvariable=self.midi_path, width=80)
        midi_entry.grid(row=0, column=1, sticky=tk.EW, padx=(6, 6))
        ttk.Button(top, text="选择…", command=self._pick_midi).grid(row=0, column=2, sticky=tk.E)
        ttk.Button(top, text="加载并显示", command=self._load_midi).grid(row=0, column=3, sticky=tk.E, padx=(6, 0))

        ttk.Label(top, text="采样目录：").grid(row=1, column=0, sticky=tk.W, pady=(8, 0))
        samples_entry = ttk.Entry(top, textvariable=self.samples_dir, width=80)
        samples_entry.grid(row=1, column=1, sticky=tk.EW, padx=(6, 6), pady=(8, 0))
        ttk.Button(top, text="选择…", command=self._pick_samples).grid(row=1, column=2, sticky=tk.E, pady=(8, 0))

        ttk.Label(top, text="音色：").grid(row=1, column=3, sticky=tk.E, padx=(6, 0), pady=(8, 0))
        self.instrument_box = ttk.Combobox(top, textvariable=self.instrument, width=18, state="readonly")
        self.instrument_box.grid(row=1, column=4, sticky=tk.E, pady=(8, 0))
        self.instrument_box.bind("<<ComboboxSelected>>", lambda _e: self._on_instrument_change())

        ttk.Label(top, text="输出WAV：").grid(row=2, column=0, sticky=tk.W, pady=(8, 0))
        out_entry = ttk.Entry(top, textvariable=self.out_wav, width=80)
        out_entry.grid(row=2, column=1, sticky=tk.EW, padx=(6, 6), pady=(8, 0))
        ttk.Button(top, text="选择…", command=self._pick_out).grid(row=2, column=2, sticky=tk.E, pady=(8, 0))

        btns = ttk.Frame(top)
        btns.grid(row=2, column=3, columnspan=2, sticky=tk.E, pady=(8, 0))
        ttk.Button(btns, text="合成WAV", command=self._render_only).pack(side=tk.LEFT)
        ttk.Button(btns, text="合成并播放", command=self._render_and_play).pack(side=tk.LEFT, padx=(6, 0))

        top.columnconfigure(1, weight=1)

        ctrl = ttk.Frame(self.root, padding=(10, 0, 10, 6))
        ctrl.pack(fill=tk.X)

        ttk.Button(ctrl, text="播放/继续", command=self._play_or_resume).pack(side=tk.LEFT)
        ttk.Button(ctrl, text="暂停", command=self._pause).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(ctrl, text="停止", command=self._stop).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(ctrl, text="回退5s", command=lambda: self._seek_delta_ms(-5000)).pack(side=tk.LEFT, padx=(16, 0))
        ttk.Button(ctrl, text="快进5s", command=lambda: self._seek_delta_ms(5000)).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(ctrl, text="重播", command=self._replay).pack(side=tk.LEFT, padx=(6, 0))

        ttk.Label(ctrl, text="速度：").pack(side=tk.LEFT, padx=(16, 0))
        self.speed_box = ttk.Combobox(ctrl, values=["50", "75", "100", "125", "150", "200"], width=6, state="readonly")
        self.speed_box.set("100")
        self.speed_box.bind("<<ComboboxSelected>>", lambda _e: self._set_speed(int(self.speed_box.get())))
        self.speed_box.pack(side=tk.LEFT)

        ttk.Checkbutton(ctrl, text="跟随播放指针", variable=self.follow_playhead).pack(side=tk.LEFT, padx=(16, 0))
        ttk.Label(ctrl, textvariable=self.play_pos_str).pack(side=tk.RIGHT)

        slider = ttk.Scale(
            self.root,
            from_=0,
            to=0,
            orient=tk.HORIZONTAL,
            variable=self.play_pos_ms,
            command=self._on_slider_change,
        )
        slider.pack(fill=tk.X, padx=10)
        slider.bind("<ButtonPress-1>", lambda _e: self._on_slider_press())
        slider.bind("<ButtonRelease-1>", lambda _e: self._on_slider_release())
        self.slider = slider

        mid = ttk.Notebook(self.root)
        mid.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        tab_roll = ttk.Frame(mid)
        tab_list = ttk.Frame(mid)
        mid.add(tab_roll, text="钢琴卷帘")
        mid.add(tab_list, text="列表")

        roll_wrap = ttk.Frame(tab_roll, padding=6)
        roll_wrap.pack(fill=tk.BOTH, expand=True)

        self.canvas = tk.Canvas(roll_wrap, background="white", highlightthickness=1, highlightbackground="#ddd")
        xsb = ttk.Scrollbar(roll_wrap, orient=tk.HORIZONTAL, command=self.canvas.xview)
        ysb = ttk.Scrollbar(roll_wrap, orient=tk.VERTICAL, command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=xsb.set, yscrollcommand=ysb.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        xsb.grid(row=1, column=0, sticky="ew")
        ysb.grid(row=0, column=1, sticky="ns")
        roll_wrap.columnconfigure(0, weight=1)
        roll_wrap.rowconfigure(0, weight=1)

        self.canvas.bind("<Motion>", self._on_canvas_motion)
        self.canvas.bind("<Button-1>", self._on_canvas_click)

        list_wrap = ttk.Frame(tab_list, padding=6)
        list_wrap.pack(fill=tk.BOTH, expand=True)

        cols = ("start", "duration", "note", "midi", "vel", "ch")
        self.tree = ttk.Treeview(list_wrap, columns=cols, show="headings", selectmode="browse")
        self.tree.heading("start", text="开始(s)")
        self.tree.heading("duration", text="时长(s)")
        self.tree.heading("note", text="音名")
        self.tree.heading("midi", text="MIDI")
        self.tree.heading("vel", text="力度")
        self.tree.heading("ch", text="通道")
        self.tree.column("start", width=90, anchor=tk.E)
        self.tree.column("duration", width=90, anchor=tk.E)
        self.tree.column("note", width=80, anchor=tk.W)
        self.tree.column("midi", width=70, anchor=tk.E)
        self.tree.column("vel", width=70, anchor=tk.E)
        self.tree.column("ch", width=70, anchor=tk.E)
        ysb2 = ttk.Scrollbar(list_wrap, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=ysb2.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        ysb2.grid(row=0, column=1, sticky="ns")
        list_wrap.columnconfigure(0, weight=1)
        list_wrap.rowconfigure(0, weight=1)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        bottom = ttk.Frame(self.root, padding=(10, 0, 10, 10))
        bottom.pack(fill=tk.X)
        ttk.Label(bottom, textvariable=self.status).pack(side=tk.LEFT)
        ttk.Label(bottom, textvariable=self.hover).pack(side=tk.RIGHT)

    def _pick_midi(self) -> None:
        p = filedialog.askopenfilename(title="选择 MIDI 文件", filetypes=[("MIDI", "*.mid *.midi"), ("All", "*.*")])
        if p:
            self.midi_path.set(p)
            if not self.out_wav.get().strip():
                base, _ = os.path.splitext(p)
                self.out_wav.set(base + ".wav")

    def _pick_samples(self) -> None:
        p = filedialog.askdirectory(title="选择采样目录")
        if p:
            self.samples_dir.set(p)
            self._refresh_instruments()

    def _pick_out(self) -> None:
        p = filedialog.asksaveasfilename(
            title="选择输出 WAV",
            defaultextension=".wav",
            filetypes=[("WAV", "*.wav"), ("All", "*.*")],
        )
        if p:
            self.out_wav.set(p)

    def _load_midi(self) -> None:
        midi = self.midi_path.get().strip()
        if not midi:
            messagebox.showerror("错误", "请先选择 MIDI 文件。")
            return
        if not os.path.isfile(midi):
            messagebox.showerror("错误", f"找不到 MIDI：{midi}")
            return

        try:
            self.status.set("正在解析 MIDI…")
            self.root.update_idletasks()
            evs, total, in_range, out_range = _note_events_from_midi(midi)
        except Exception as e:
            self.status.set("解析失败")
            messagebox.showerror("解析失败", str(e))
            return

        self.events = evs
        self.event_starts = [e.start_s for e in evs]
        self.total_seconds = total
        self._render_table()
        self._render_piano_roll()
        self._sync_timeline_total(int(total * 1000))
        if not evs:
            self.status.set(
                f"已加载：{os.path.basename(midi)}  |  可用音符数：0  |  超出范围：{out_range}  |  时长：{total:.2f}s"
            )
        else:
            self.status.set(
                f"已加载：{os.path.basename(midi)}  |  可用音符数：{in_range}  |  超出范围：{out_range}  |  时长：{total:.2f}s"
            )

    def _render_table(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for idx, e in enumerate(self.events):
            self.tree.insert(
                "",
                tk.END,
                iid=str(idx),
                values=(f"{e.start_s:.3f}", f"{e.duration_s:.3f}", e.note_name, str(e.note), str(e.velocity), str(e.channel)),
            )

    def _render_piano_roll(self) -> None:
        self.canvas.delete("all")
        self.rect_tags = []
        self.selected_idx = None

        px_per_sec = self.px_per_sec
        row_h = 8
        left = self.roll_left
        top = self.roll_top
        max_note = self.max_note
        min_note = self.min_note

        total_w = int(self.total_seconds * px_per_sec) + left + 60
        if total_w < 1000:
            total_w = 1000
        total_h = (max_note - min_note + 1) * row_h + top + 20
        if total_h < 600:
            total_h = 600
        self.roll_w = max(1, total_w)
        self.roll_h = max(1, total_h)
        self.canvas.configure(scrollregion=(0, 0, total_w, total_h))

        sec_marks = int(self.total_seconds) + 1
        for s in range(sec_marks + 1):
            x = left + int(s * px_per_sec)
            self.canvas.create_line(x, top, x, total_h - 10, fill="#eee")
            self.canvas.create_text(x + 2, 8, text=str(s), anchor="nw", fill="#666")

        for n in range(min_note, max_note + 1):
            y = top + (max_note - n) * row_h
            if n % 12 in (1, 3, 6, 8, 10):
                self.canvas.create_rectangle(0, y, total_w, y + row_h, fill="#fafafa", outline="")
            if n % 12 == 0:
                self.canvas.create_text(6, y, text=engine.midi_to_note_name(n), anchor="nw", fill="#666")

        for idx, e in enumerate(self.events):
            x0 = left + int(e.start_s * px_per_sec)
            x1 = left + max(1, int(e.end_s * px_per_sec))
            y0 = top + (max_note - e.note) * row_h
            y1 = y0 + row_h - 1
            tag = f"n{idx}"
            self.rect_tags.append(tag)
            self.canvas.create_rectangle(x0, y0, x1, y1, fill=_vel_color(e.velocity), outline="", tags=(tag, "note"))

        x = left + int((self.play_pos_ms.get() / 1000.0) * px_per_sec)
        self.canvas.create_line(x, top, x, total_h - 10, fill="#d33", width=2, tags=("playhead",))
        if not self.events:
            self.canvas.create_text(left + 10, top + 10, text="未检测到可用音符事件", anchor="nw", fill="#999")

    def _on_canvas_motion(self, event: tk.Event) -> None:
        item = self.canvas.find_withtag("current")
        if not item:
            self.hover.set("")
            return
        tags = self.canvas.gettags(item[0])
        idx = None
        for t in tags:
            if t.startswith("n"):
                try:
                    idx = int(t[1:])
                except Exception:
                    idx = None
                break
        if idx is None or idx < 0 or idx >= len(self.events):
            self.hover.set("")
            return
        e = self.events[idx]
        self.hover.set(f"{e.note_name}  |  start {e.start_s:.3f}s  |  dur {e.duration_s:.3f}s")

    def _on_canvas_click(self, event: tk.Event) -> None:
        item = self.canvas.find_withtag("current")
        if not item:
            self._seek_by_canvas_click(event)
            return
        tags = self.canvas.gettags(item[0])
        idx = None
        for t in tags:
            if t.startswith("n"):
                try:
                    idx = int(t[1:])
                except Exception:
                    idx = None
                break
        if idx is None:
            self._seek_by_canvas_click(event)
            return
        self._select_idx(idx, from_tree=False)

    def _on_tree_select(self, event: tk.Event) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        try:
            idx = int(sel[0])
        except Exception:
            return
        self._select_idx(idx, from_tree=True)

    def _select_idx(self, idx: int, from_tree: bool) -> None:
        self._select_idx_opts(idx, from_tree=from_tree, scroll_x=True, scroll_y=True)

    def _select_idx_opts(self, idx: int, from_tree: bool, scroll_x: bool, scroll_y: bool) -> None:
        if idx < 0 or idx >= len(self.events):
            return
        if self.selected_idx is not None and 0 <= self.selected_idx < len(self.rect_tags):
            old_tag = self.rect_tags[self.selected_idx]
            self.canvas.itemconfigure(old_tag, outline="", width=1)

        self.selected_idx = idx
        tag = self.rect_tags[idx] if idx < len(self.rect_tags) else None
        if tag:
            self.canvas.itemconfigure(tag, outline="#222", width=1)
            bbox = self.canvas.bbox(tag)
            if bbox:
                x0, y0, x1, y1 = bbox
                if scroll_x:
                    self._ensure_visible_x(int(x0), margin=120)
                if scroll_y:
                    self._ensure_visible_y(int(y0), int(y1), margin=80)

        if not from_tree:
            self.tree.selection_set(str(idx))
            self.tree.see(str(idx))

    def _render_only(self) -> None:
        self._render(play=False)

    def _render_and_play(self) -> None:
        self._render(play=True)

    def _render(self, play: bool) -> None:
        midi = self.midi_path.get().strip()
        if not midi:
            messagebox.showerror("错误", "请先选择 MIDI 文件。")
            return
        if not os.path.isfile(midi):
            messagebox.showerror("错误", f"找不到 MIDI：{midi}")
            return

        out = self.out_wav.get().strip()
        if not out:
            base, _ = os.path.splitext(midi)
            out = base + ".wav"
            self.out_wav.set(out)

        samples = self._current_samples_path()
        if not samples:
            messagebox.showerror("错误", "没有检测到可用音色目录。请检查采样目录与音色选择。")
            return

        def work() -> None:
            try:
                self._set_status_from_thread("正在合成 WAV…")
                tpq, tempos, spans = engine.parse_midi(midi)
                engine.render(
                    spans=spans,
                    tpq=tpq,
                    tempos=tempos,
                    sample_root=samples,
                    out_wav=out,
                    target_sr=44100,
                    normalize_peak=28000,
                )
                self._set_status_from_thread(f"已输出：{out}")
                self._after_from_thread(lambda: self._after_render(out, play))
            except Exception as e:
                self._show_error_from_thread(str(e))

        threading.Thread(target=work, daemon=True).start()

    def _after_from_thread(self, fn) -> None:
        self.root.after(0, fn)

    def _after_render(self, out_wav: str, play: bool) -> None:
        self.play_wav_path = out_wav
        try:
            self.player.open(out_wav)
            self.play_total_ms = max(self.player.length_ms(), int(self.total_seconds * 1000))
        except Exception as e:
            messagebox.showerror("播放初始化失败", str(e))
            return
        ok = self.player.set_speed(self.speed_percent.get())
        if not ok:
            self.speed_percent.set(100)
            self.speed_box.set("100")
            self.speed_box.configure(state="disabled")
            self.status.set("提示：当前系统不支持变速播放，已禁用速度功能。")
        else:
            self.speed_box.configure(state="readonly")
        self._sync_timeline_total(self.play_total_ms)
        if play:
            self._play_from_ms(0)

    def _sync_timeline_total(self, total_ms: int) -> None:
        total_ms = max(0, int(total_ms))
        self.play_total_ms = total_ms
        self.slider.configure(to=total_ms if total_ms > 0 else 0)
        cur = min(self.play_pos_ms.get(), total_ms)
        self.play_pos_ms.set(cur)
        self.play_pos_str.set(f"{_fmt_ms(cur)} / {_fmt_ms(total_ms)}")

    def _play_from_ms(self, start_ms: int) -> None:
        if not self.play_wav_path:
            return
        try:
            if not self.player.opened or self.player.path != self.play_wav_path:
                self.player.open(self.play_wav_path)
                ok = self.player.set_speed(self.speed_percent.get())
                if not ok:
                    self.speed_percent.set(100)
                    self.speed_box.set("100")
                    self.speed_box.configure(state="disabled")
            self.player.play(start_ms=start_ms)
            self.play_state.set("playing")
            self.status.set("正在播放…")
        except Exception as e:
            messagebox.showerror("播放失败", str(e))

    def _play_or_resume(self) -> None:
        if not self.play_wav_path:
            self._render(play=True)
            return
        mode = self.player.state()
        if mode in ("paused",):
            try:
                self.player.resume()
                self.play_state.set("playing")
                self.status.set("正在播放…")
            except Exception as e:
                messagebox.showerror("继续失败", str(e))
            return
        if mode in ("playing",):
            return
        self._play_from_ms(self.play_pos_ms.get())

    def _pause(self) -> None:
        try:
            self.player.pause()
            self.play_state.set("paused")
            self.status.set("已暂停")
        except Exception as e:
            messagebox.showerror("暂停失败", str(e))

    def _stop(self) -> None:
        try:
            self.player.stop()
            self.play_state.set("stopped")
            self.status.set("已停止")
        except Exception as e:
            messagebox.showerror("停止失败", str(e))

    def _replay(self) -> None:
        self.play_pos_ms.set(0)
        self._move_playhead(0)
        self._play_from_ms(0)

    def _seek_delta_ms(self, delta: int) -> None:
        cur = self.play_pos_ms.get()
        nxt = cur + int(delta)
        if nxt < 0:
            nxt = 0
        if self.play_total_ms > 0 and nxt > self.play_total_ms:
            nxt = self.play_total_ms
        self._seek_to_ms(nxt, keep_playing=self.player.state() == "playing")

    def _seek_to_ms(self, pos_ms: int, keep_playing: bool) -> None:
        pos_ms = max(0, int(pos_ms))
        if self.play_total_ms > 0:
            pos_ms = min(pos_ms, self.play_total_ms)
        self.play_pos_ms.set(pos_ms)
        self._move_playhead(pos_ms)
        self._follow_views(pos_ms / 1000.0)
        try:
            self.player.seek(pos_ms)
            if keep_playing:
                self.player.play(start_ms=pos_ms)
                self.play_state.set("playing")
        except Exception as e:
            messagebox.showerror("跳转失败", str(e))

    def _set_speed(self, percent: int) -> None:
        self.speed_percent.set(int(percent))
        ok = self.player.set_speed(int(percent))
        if not ok:
            self.speed_percent.set(100)
            self.speed_box.set("100")
            self.speed_box.configure(state="disabled")
            self.status.set("提示：当前系统不支持变速播放，已禁用速度功能。")

    def _on_slider_press(self) -> None:
        self.is_seeking = True

    def _on_slider_release(self) -> None:
        pos = self.play_pos_ms.get()
        keep = self.player.state() == "playing"
        self.is_seeking = False
        self._seek_to_ms(pos, keep_playing=keep)

    def _on_slider_change(self, _value: str) -> None:
        if self.is_seeking:
            pos = self.play_pos_ms.get()
            self.play_pos_str.set(f"{_fmt_ms(pos)} / {_fmt_ms(self.play_total_ms)}")
            self._move_playhead(pos)

    def _move_playhead(self, pos_ms: int) -> None:
        x = self.roll_left + int((pos_ms / 1000.0) * self.px_per_sec)
        items = self.canvas.find_withtag("playhead")
        if items:
            self.canvas.coords(items[0], x, self.roll_top, x, self.roll_h - 10)
        if self.follow_playhead.get() and self.roll_w > 1:
            self._ensure_visible_x(x, margin=160)

    def _ensure_visible_x(self, x: int, margin: int) -> None:
        if self.roll_w <= 1:
            return
        view_w = int(self.canvas.winfo_width())
        if view_w <= 0:
            return
        vx0, _vx1 = self.canvas.xview()
        left = vx0 * float(self.roll_w)
        right = left + float(view_w)
        new_left = None
        if float(x) < left + float(margin):
            new_left = float(x) - float(margin)
        elif float(x) > right - float(margin):
            new_left = float(x) - (float(view_w) - float(margin))
        if new_left is None:
            return
        max_left = float(max(0, self.roll_w - view_w))
        if new_left < 0:
            new_left = 0.0
        if new_left > max_left:
            new_left = max_left
        self.canvas.xview_moveto(new_left / float(self.roll_w))

    def _ensure_visible_y(self, y0: int, y1: int, margin: int) -> None:
        if self.roll_h <= 1:
            return
        view_h = int(self.canvas.winfo_height())
        if view_h <= 0:
            return
        vy0, _vy1 = self.canvas.yview()
        top = vy0 * float(self.roll_h)
        bottom = top + float(view_h)
        new_top = None
        if float(y0) < top + float(margin):
            new_top = float(y0) - float(margin)
        elif float(y1) > bottom - float(margin):
            new_top = float(y1) - (float(view_h) - float(margin))
        if new_top is None:
            return
        max_top = float(max(0, self.roll_h - view_h))
        if new_top < 0:
            new_top = 0.0
        if new_top > max_top:
            new_top = max_top
        self.canvas.yview_moveto(new_top / float(self.roll_h))

    def _seek_by_canvas_click(self, event: tk.Event) -> None:
        if self.play_total_ms <= 0:
            return
        x = int(self.canvas.canvasx(event.x))
        pos_px = x - self.roll_left
        if pos_px < 0:
            pos_px = 0
        pos_s = pos_px / float(self.px_per_sec)
        pos_ms = int(pos_s * 1000)
        keep = self.player.state() == "playing"
        self._seek_to_ms(pos_ms, keep_playing=keep)

    def _schedule_tick(self) -> None:
        self.root.after(80, self._tick)

    def _tick(self) -> None:
        try:
            if self.player.opened and not self.is_seeking:
                mode = self.player.state()
                if mode == "playing":
                    pos = self.player.position_ms()
                    if self.play_total_ms > 0 and pos > self.play_total_ms:
                        pos = self.play_total_ms
                    self.play_pos_ms.set(pos)
                    self.play_pos_str.set(f"{_fmt_ms(pos)} / {_fmt_ms(self.play_total_ms)}")
                    self._move_playhead(pos)
                    self._follow_views(pos / 1000.0)
                elif mode in ("stopped", "closed"):
                    if self.play_state.get() == "playing":
                        self.play_state.set("stopped")
                        self.status.set("播放结束")
        except Exception:
            pass
        finally:
            self._schedule_tick()

    def _set_status_from_thread(self, text: str) -> None:
        def _do() -> None:
            self.status.set(text)
        self.root.after(0, _do)

    def _show_error_from_thread(self, text: str) -> None:
        def _do() -> None:
            self.status.set("失败")
            messagebox.showerror("失败", text)
        self.root.after(0, _do)

    def _on_close(self) -> None:
        try:
            self.player.close()
        except Exception:
            pass
        self.root.destroy()

    def _refresh_instruments(self) -> None:
        root = self.samples_dir.get().strip()
        opts = _scan_instruments(root) if root else []
        self._instrument_options = opts
        names = [n for (n, _p) in opts]
        self.instrument_box.configure(values=names)
        if names:
            cur = self.instrument.get().strip()
            if cur not in names:
                self.instrument.set(names[0])
        else:
            self.instrument.set("")

    def _on_instrument_change(self) -> None:
        self.last_follow_idx = None

    def _current_samples_path(self) -> str:
        root = self.samples_dir.get().strip()
        if not root:
            return ""
        if not self._instrument_options:
            self._refresh_instruments()
        name = self.instrument.get().strip()
        for n, p in self._instrument_options:
            if n == name:
                return p
        if _is_valid_88key_sample_dir(root):
            return root
        return ""

    def _follow_views(self, pos_s: float) -> None:
        if not self.events or not self.event_starts:
            return
        t = float(pos_s)
        lo = 0
        hi = len(self.event_starts) - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            if self.event_starts[mid] <= t:
                lo = mid + 1
            else:
                hi = mid - 1
        idx = max(0, lo - 1)
        if idx < len(self.events) and self.events[idx].end_s < t and idx + 1 < len(self.events):
            idx = idx + 1
        if self.last_follow_idx == idx:
            return
        self.last_follow_idx = idx
        self._select_idx_opts(idx, from_tree=False, scroll_x=False, scroll_y=True)


def main() -> int:
    root = tk.Tk()
    ttk.Style().theme_use("clam")
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
