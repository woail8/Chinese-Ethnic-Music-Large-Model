import argparse
import os
import re
import sys
from dataclasses import dataclass
from typing import List, Optional, Tuple


MAJOR_SCALE_OFFSETS = {1: 0, 2: 2, 3: 4, 4: 5, 5: 7, 6: 9, 7: 11}
NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def midi_to_note_name(midi: int) -> str:
    name = NOTE_NAMES[midi % 12]
    octave = (midi // 12) - 1
    return f"{name}{octave}"


def note_name_to_midi(name: str) -> int:
    s = name.strip()
    m = re.match(r"^([A-Ga-g])([#b]?)(-?\d+)$", s)
    if not m:
        raise ValueError("do 音名格式应为 C4、D#3、Bb2 这种形式。")
    letter = m.group(1).upper()
    acc = m.group(2)
    octave = int(m.group(3))
    base_map = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
    semitone = base_map[letter]
    if acc == "#":
        semitone += 1
    elif acc == "b":
        semitone -= 1
    return (octave + 1) * 12 + semitone


def parse_time_signature(ts: str) -> Tuple[int, int]:
    s = ts.strip()
    m = re.match(r"^(\d+)\s*/\s*(\d+)$", s)
    if not m:
        raise ValueError("拍号格式应为 4/4、3/4、6/8 这种形式。")
    num = int(m.group(1))
    den = int(m.group(2))
    if num <= 0:
        raise ValueError("拍号分子必须 > 0。")
    if den not in (1, 2, 4, 8, 16, 32):
        raise ValueError("拍号分母建议为 1/2/4/8/16/32。")
    return num, den


def parse_key_signature(key: str) -> Tuple[int, int]:
    s = key.strip()
    if not s:
        return 0, 0
    m = re.match(r"^([A-Ga-g])([#b]?)(?:\s+(major|minor))?$", s)
    if not m:
        raise ValueError('调号格式示例：C major、G major、F# minor、Bb major；或仅写 C、G、F#。')
    letter = m.group(1).upper()
    acc = m.group(2)
    mode = (m.group(3) or "major").lower()
    mi = 0 if mode == "major" else 1

    major_sf = {
        "C": 0,
        "G": 1,
        "D": 2,
        "A": 3,
        "E": 4,
        "B": 5,
        "F#": 6,
        "C#": 7,
        "F": -1,
        "Bb": -2,
        "Eb": -3,
        "Ab": -4,
        "Db": -5,
        "Gb": -6,
        "Cb": -7,
    }
    minor_sf = {
        "A": 0,
        "E": 1,
        "B": 2,
        "F#": 3,
        "C#": 4,
        "G#": 5,
        "D#": 6,
        "A#": 7,
        "D": -1,
        "G": -2,
        "C": -3,
        "F": -4,
        "Bb": -5,
        "Eb": -6,
        "Ab": -7,
    }

    tonic = letter + acc
    sf = (major_sf if mi == 0 else minor_sf).get(tonic)
    if sf is None:
        raise ValueError("不支持的调号。")
    return int(sf), int(mi)


def vlq(n: int) -> bytes:
    n = int(n)
    if n < 0:
        n = 0
    out = bytearray()
    out.append(n & 0x7F)
    n >>= 7
    while n:
        out.append(0x80 | (n & 0x7F))
        n >>= 7
    out.reverse()
    return bytes(out)


def u16be(n: int) -> bytes:
    return bytes([(n >> 8) & 0xFF, n & 0xFF])


def u32be(n: int) -> bytes:
    return bytes([(n >> 24) & 0xFF, (n >> 16) & 0xFF, (n >> 8) & 0xFF, n & 0xFF])


@dataclass
class NoteToken:
    kind: str
    degree: Optional[int] = None
    accidental: int = 0
    octave_shift: int = 0
    underscores: int = 0
    dotted: bool = False


@dataclass
class NoteSpan:
    start_tick: int
    end_tick: int
    note: int
    velocity: int


def tokenize(text: str) -> List[NoteToken]:
    tokens: List[NoteToken] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if ch == "|":
            tokens.append(NoteToken(kind="bar"))
            i += 1
            continue
        if ch == "-":
            tokens.append(NoteToken(kind="extend"))
            i += 1
            continue
        acc = 0
        if ch == "#":
            acc = 1
            i += 1
        elif ch == "b":
            acc = -1
            i += 1
        if i >= n or not text[i].isdigit():
            raise ValueError(f"无法解析符号：位置 {i + 1}")
        degree = int(text[i])
        if degree < 0 or degree > 7:
            raise ValueError(f"音符数字必须在 0-7：位置 {i + 1}")
        i += 1
        octave_shift = 0
        while i < n and text[i] in ("˙", "⸳"):
            if text[i] == "˙":
                octave_shift += 1
            else:
                octave_shift -= 1
            i += 1
        underscores = 0
        while i < n and text[i] == "_":
            underscores += 1
            i += 1
        dotted = False
        if i < n and text[i] == "·":
            dotted = True
            i += 1
        tokens.append(
            NoteToken(
                kind="note",
                degree=degree,
                accidental=acc,
                octave_shift=octave_shift,
                underscores=underscores,
                dotted=dotted,
            )
        )
    return tokens


def duration_beats_from_token(token: NoteToken, no_underscore_beats: float) -> float:
    if token.underscores <= 0:
        beats = float(no_underscore_beats)
    elif token.underscores == 1:
        beats = 1.0
    elif token.underscores == 2:
        beats = 0.25
    elif token.underscores == 3:
        beats = 0.125
    else:
        beats = 0.125 / (2 ** (token.underscores - 3))
    if token.dotted:
        beats *= 1.5
    return beats


def degree_to_midi(
    degree: int, *, do_midi: int, accidental: int, octave_shift: int, mode: str
) -> int:
    if degree == 0:
        return 0
    if mode != "major":
        raise ValueError("当前仅支持 major 音阶映射。")
    base = MAJOR_SCALE_OFFSETS.get(degree)
    if base is None:
        raise ValueError("音符数字必须在 1-7（或 0 表示休止）。")
    midi = do_midi + base + accidental + 12 * octave_shift
    return midi


def build_spans(
    tokens: List[NoteToken],
    *,
    do_midi: int,
    bpm: int,
    time_sig: Tuple[int, int],
    tpq: int,
    no_underscore_beats: float,
    velocity: int,
    mode: str,
) -> Tuple[List[NoteSpan], int]:
    _, den = time_sig
    beat_ticks = int(round(tpq * (4.0 / float(den))))
    if beat_ticks <= 0:
        beat_ticks = tpq

    cur_tick = 0
    spans: List[NoteSpan] = []
    last_span_idx: Optional[int] = None
    last_token_was_note_or_rest = False

    for tok in tokens:
        if tok.kind == "bar":
            continue
        if tok.kind == "extend":
            if not last_token_was_note_or_rest:
                continue
            cur_tick += beat_ticks
            if last_span_idx is not None:
                spans[last_span_idx].end_tick += beat_ticks
            continue
        if tok.kind != "note":
            continue

        beats = duration_beats_from_token(tok, no_underscore_beats=no_underscore_beats)
        dur_ticks = int(round(beats * beat_ticks))
        if dur_ticks <= 0:
            dur_ticks = 1

        if tok.degree == 0:
            cur_tick += dur_ticks
            last_span_idx = None
            last_token_was_note_or_rest = True
            continue

        midi_note = degree_to_midi(
            int(tok.degree),
            do_midi=do_midi,
            accidental=int(tok.accidental),
            octave_shift=int(tok.octave_shift),
            mode=mode,
        )
        if midi_note < 0 or midi_note > 127:
            raise ValueError(f"音高超出 MIDI 范围：{midi_to_note_name(midi_note)}")

        start = cur_tick
        end = cur_tick + dur_ticks
        spans.append(NoteSpan(start_tick=start, end_tick=end, note=midi_note, velocity=velocity))
        last_span_idx = len(spans) - 1
        last_token_was_note_or_rest = True
        cur_tick = end

    total_ticks = cur_tick
    return spans, total_ticks


def write_midi(
    out_path: str,
    *,
    spans: List[NoteSpan],
    total_ticks: int,
    tpq: int,
    bpm: int,
    time_sig: Tuple[int, int],
    key_sig: Tuple[int, int] = (0, 0),
) -> None:
    num, den = time_sig
    tempo_us = int(round(60_000_000 / max(1, int(bpm))))
    dd = 0
    v = int(den)
    while v > 1:
        v //= 2
        dd += 1

    events: List[Tuple[int, int, bytes]] = []

    tempo = bytes([0xFF, 0x51, 0x03, (tempo_us >> 16) & 0xFF, (tempo_us >> 8) & 0xFF, tempo_us & 0xFF])
    ts = bytes([0xFF, 0x58, 0x04, num & 0xFF, dd & 0xFF, 24, 8])
    sf, mi = key_sig
    if sf < -7:
        sf = -7
    if sf > 7:
        sf = 7
    ks = bytes([0xFF, 0x59, 0x02, sf & 0xFF, 1 if int(mi) else 0])
    events.append((0, 0, tempo))
    events.append((0, 0, ts))
    events.append((0, 0, ks))

    for sp in spans:
        on = bytes([0x90, sp.note & 0x7F, sp.velocity & 0x7F])
        off = bytes([0x90, sp.note & 0x7F, 0])
        events.append((sp.start_tick, 1, on))
        events.append((sp.end_tick, 0, off))

    end = bytes([0xFF, 0x2F, 0x00])
    events.append((max(total_ticks, 0), 2, end))

    events.sort(key=lambda x: (x[0], x[1]))

    track = bytearray()
    last_tick = 0
    for tick, _, payload in events:
        dt = int(tick) - last_tick
        if dt < 0:
            dt = 0
        track += vlq(dt)
        track += payload
        last_tick = int(tick)

    header = bytearray()
    header += b"MThd"
    header += u32be(6)
    header += u16be(0)
    header += u16be(1)
    header += u16be(tpq)

    trk = bytearray()
    trk += b"MTrk"
    trk += u32be(len(track))
    trk += track

    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(header)
        f.write(trk)


def build_midi_bytes(
    *,
    spans: List[NoteSpan],
    total_ticks: int,
    tpq: int,
    bpm: int,
    time_sig: Tuple[int, int],
    key_sig: Tuple[int, int] = (0, 0),
) -> bytes:
    tmp = bytearray()
    num, den = time_sig
    tempo_us = int(round(60_000_000 / max(1, int(bpm))))
    dd = 0
    v = int(den)
    while v > 1:
        v //= 2
        dd += 1

    events: List[Tuple[int, int, bytes]] = []
    tempo = bytes([0xFF, 0x51, 0x03, (tempo_us >> 16) & 0xFF, (tempo_us >> 8) & 0xFF, tempo_us & 0xFF])
    ts = bytes([0xFF, 0x58, 0x04, num & 0xFF, dd & 0xFF, 24, 8])
    sf, mi = key_sig
    if sf < -7:
        sf = -7
    if sf > 7:
        sf = 7
    ks = bytes([0xFF, 0x59, 0x02, sf & 0xFF, 1 if int(mi) else 0])
    events.append((0, 0, tempo))
    events.append((0, 0, ts))
    events.append((0, 0, ks))

    for sp in spans:
        on = bytes([0x90, sp.note & 0x7F, sp.velocity & 0x7F])
        off = bytes([0x90, sp.note & 0x7F, 0])
        events.append((sp.start_tick, 1, on))
        events.append((sp.end_tick, 0, off))

    end = bytes([0xFF, 0x2F, 0x00])
    events.append((max(total_ticks, 0), 2, end))
    events.sort(key=lambda x: (x[0], x[1]))

    track = bytearray()
    last_tick = 0
    for tick, _, payload in events:
        dt = int(tick) - last_tick
        if dt < 0:
            dt = 0
        track += vlq(dt)
        track += payload
        last_tick = int(tick)

    header = bytearray()
    header += b"MThd"
    header += u32be(6)
    header += u16be(0)
    header += u16be(1)
    header += u16be(tpq)

    trk = bytearray()
    trk += b"MTrk"
    trk += u32be(len(track))
    trk += track

    tmp += header
    tmp += trk
    return bytes(tmp)


def read_text_input(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--text", default="", help="乐谱文本（按规则的序列）")
    parser.add_argument("--in", dest="in_path", default="", help="从 .txt 读取乐谱文本")
    parser.add_argument("--out", default="", help="输出 .mid 路径")
    parser.add_argument("--do", dest="do_note", default="C4", help="1(do) 对应音名，如 C4、D#4、Bb3")
    parser.add_argument("--key", default="C major", help='调号，如 "C major"、"F# minor"、"Bb major"')
    parser.add_argument("--bpm", type=int, default=120, help="速度 BPM")
    parser.add_argument("--ts", default="4/4", help="拍号，如 4/4、3/4、6/8")
    parser.add_argument("--tpq", type=int, default=480, help="ticks per quarter")
    parser.add_argument("--no-underscore-beats", type=float, default=2.0, help="无下划线时的时值（以拍为单位）")
    parser.add_argument("--vel", type=int, default=96, help="力度 1-127")
    args = parser.parse_args()

    text = args.text.strip()
    if not text and args.in_path:
        text = read_text_input(args.in_path).strip()
    if not text:
        print("缺少输入：请提供 --text 或 --in", file=sys.stderr)
        return 2

    do_midi = note_name_to_midi(args.do_note)
    time_sig = parse_time_signature(args.ts)
    key_sig = parse_key_signature(args.key)
    vel = int(args.vel)
    if vel < 1:
        vel = 1
    if vel > 127:
        vel = 127

    tokens = tokenize(text)
    spans, total_ticks = build_spans(
        tokens,
        do_midi=do_midi,
        bpm=int(args.bpm),
        time_sig=time_sig,
        tpq=int(args.tpq),
        no_underscore_beats=float(args.no_underscore_beats),
        velocity=vel,
        mode="major",
    )

    out_path = args.out.strip()
    if not out_path:
        if args.in_path:
            base, _ = os.path.splitext(os.path.abspath(args.in_path))
            out_path = base + ".mid"
        else:
            out_path = os.path.abspath("out.mid")
    write_midi(
        out_path,
        spans=spans,
        total_ticks=total_ticks,
        tpq=int(args.tpq),
        bpm=int(args.bpm),
        time_sig=time_sig,
        key_sig=key_sig,
    )
    print(f"已生成：{out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
