import argparse
import os
import sys
import wave
from dataclasses import dataclass
from array import array
from typing import Dict, List, Optional, Tuple


NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def midi_to_note_name(midi: int) -> str:
    name = NOTE_NAMES[midi % 12]
    octave = (midi // 12) - 1
    return f"{name}{octave}"


def read_u16be(b: bytes, off: int) -> int:
    return (b[off] << 8) | b[off + 1]


def read_u32be(b: bytes, off: int) -> int:
    return (b[off] << 24) | (b[off + 1] << 16) | (b[off + 2] << 8) | b[off + 3]


def read_vlq(b: bytes, off: int) -> Tuple[int, int]:
    value = 0
    while True:
        byte = b[off]
        off += 1
        value = (value << 7) | (byte & 0x7F)
        if (byte & 0x80) == 0:
            return value, off


@dataclass(frozen=True)
class TempoEvent:
    tick: int
    us_per_qn: int


@dataclass(frozen=True)
class NoteSpan:
    note: int
    channel: int
    start_tick: int
    end_tick: int
    velocity: int


def parse_midi(midi_path: str) -> Tuple[int, List[TempoEvent], List[NoteSpan]]:
    with open(midi_path, "rb") as f:
        data = f.read()

    if data[:4] != b"MThd":
        raise ValueError("不是有效的 MIDI 文件（缺少 MThd）。")
    header_len = read_u32be(data, 4)
    if header_len < 6:
        raise ValueError("MThd 长度异常。")
    fmt = read_u16be(data, 8)
    ntrks = read_u16be(data, 10)
    division = read_u16be(data, 12)
    if division & 0x8000:
        raise ValueError("不支持 SMPTE 时间基的 MIDI（division 最高位为 1）。")
    tpq = division
    if tpq <= 0:
        raise ValueError("ticks_per_quarter 无效。")

    offset = 8 + header_len
    tempos: List[TempoEvent] = []
    spans: List[NoteSpan] = []

    for _ in range(ntrks):
        if data[offset : offset + 4] != b"MTrk":
            raise ValueError("不是有效的 MIDI 文件（缺少 MTrk）。")
        trk_len = read_u32be(data, offset + 4)
        trk_start = offset + 8
        trk_end = trk_start + trk_len
        offset = trk_end

        t_off = trk_start
        abs_tick = 0
        running_status: Optional[int] = None
        active: Dict[Tuple[int, int], List[Tuple[int, int]]] = {}

        while t_off < trk_end:
            delta, t_off = read_vlq(data, t_off)
            abs_tick += delta

            status = data[t_off]
            if status < 0x80:
                if running_status is None:
                    raise ValueError("running status 出现但之前没有 status。")
                status = running_status
            else:
                t_off += 1
                running_status = status

            if status == 0xFF:
                running_status = None
                meta_type = data[t_off]
                t_off += 1
                length, t_off = read_vlq(data, t_off)
                meta = data[t_off : t_off + length]
                t_off += length
                if meta_type == 0x2F:
                    break
                if meta_type == 0x51 and length == 3:
                    us = (meta[0] << 16) | (meta[1] << 8) | meta[2]
                    tempos.append(TempoEvent(abs_tick, us))
                continue

            if status == 0xF0 or status == 0xF7:
                running_status = None
                length, t_off = read_vlq(data, t_off)
                t_off += length
                continue

            if status >= 0xF0:
                running_status = None
                if status == 0xF2:
                    t_off += 2
                elif status in (0xF1, 0xF3):
                    t_off += 1
                else:
                    t_off += 0
                continue

            msg = status & 0xF0
            ch = status & 0x0F

            if msg in (0xC0, 0xD0):
                b1 = data[t_off]
                t_off += 1
                continue

            b1 = data[t_off]
            b2 = data[t_off + 1]
            t_off += 2

            if msg == 0x90:
                note = b1
                vel = b2
                key = (ch, note)
                if vel == 0:
                    stack = active.get(key)
                    if stack:
                        start_tick, start_vel = stack.pop()
                        spans.append(NoteSpan(note, ch, start_tick, abs_tick, start_vel))
                else:
                    active.setdefault(key, []).append((abs_tick, vel))
            elif msg == 0x80:
                note = b1
                key = (ch, note)
                stack = active.get(key)
                if stack:
                    start_tick, start_vel = stack.pop()
                    spans.append(NoteSpan(note, ch, start_tick, abs_tick, start_vel))

        for (ch, note), stack in active.items():
            for start_tick, start_vel in stack:
                spans.append(NoteSpan(note, ch, start_tick, abs_tick, start_vel))

    if not tempos:
        tempos = [TempoEvent(0, 500000)]
    else:
        tempos.sort(key=lambda x: x.tick)
        if tempos[0].tick != 0:
            tempos = [TempoEvent(0, 500000)] + tempos

    return tpq, tempos, spans


@dataclass(frozen=True)
class TempoMapPoint:
    tick: int
    us_per_qn: int
    seconds_at_tick: float


def build_tempo_map(tpq: int, tempos: List[TempoEvent]) -> List[TempoMapPoint]:
    points: List[TempoMapPoint] = []
    last_tick = tempos[0].tick
    last_us = tempos[0].us_per_qn
    seconds = 0.0
    points.append(TempoMapPoint(last_tick, last_us, seconds))
    for ev in tempos[1:]:
        if ev.tick < last_tick:
            continue
        dt = ev.tick - last_tick
        seconds += (dt / tpq) * (last_us / 1_000_000.0)
        last_tick = ev.tick
        last_us = ev.us_per_qn
        points.append(TempoMapPoint(last_tick, last_us, seconds))
    return points


def tick_to_seconds(tick: int, tpq: int, points: List[TempoMapPoint]) -> float:
    lo = 0
    hi = len(points) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if points[mid].tick <= tick:
            lo = mid + 1
        else:
            hi = mid - 1
    idx = max(0, lo - 1)
    p = points[idx]
    return p.seconds_at_tick + ((tick - p.tick) / tpq) * (p.us_per_qn / 1_000_000.0)


@dataclass(frozen=True)
class Sample:
    pcm16_mono: array
    sample_rate: int


def _pcm_bytes_to_int16_mono(frames: bytes, sampwidth: int, nchannels: int) -> array:
    if nchannels <= 0:
        raise ValueError("nchannels 无效。")
    if sampwidth not in (1, 2, 3, 4):
        raise ValueError("不支持的 WAV sampwidth。")

    if sampwidth == 2:
        a = array("h")
        a.frombytes(frames)
        if sys.byteorder != "little":
            a.byteswap()
        if nchannels == 1:
            return a
        out = array("h")
        out.extend([0] * (len(a) // nchannels))
        o = 0
        for i in range(0, len(a), nchannels):
            s = 0
            for c in range(nchannels):
                s += int(a[i + c])
            out[o] = int(s / nchannels)
            o += 1
        return out

    total_samples = len(frames) // sampwidth
    if total_samples % nchannels != 0:
        total_samples = (total_samples // nchannels) * nchannels
    total_frames = total_samples // nchannels

    out = array("h")
    out.extend([0] * total_frames)
    idx = 0
    for f in range(total_frames):
        s = 0
        for _ in range(nchannels):
            if sampwidth == 1:
                v = frames[idx] - 128
                idx += 1
                s += v << 8
            elif sampwidth == 3:
                b0 = frames[idx]
                b1 = frames[idx + 1]
                b2 = frames[idx + 2]
                idx += 3
                v = b0 | (b1 << 8) | (b2 << 16)
                if v & 0x800000:
                    v -= 0x1000000
                s += v >> 8
            else:
                b0 = frames[idx]
                b1 = frames[idx + 1]
                b2 = frames[idx + 2]
                b3 = frames[idx + 3]
                idx += 4
                v = b0 | (b1 << 8) | (b2 << 16) | (b3 << 24)
                if v & 0x80000000:
                    v -= 0x100000000
                s += v >> 16

        m = int(s / nchannels)
        if m > 32767:
            m = 32767
        elif m < -32768:
            m = -32768
        out[f] = m
    return out


def _resample_linear(samples: array, src_sr: int, dst_sr: int) -> array:
    if src_sr <= 0 or dst_sr <= 0:
        raise ValueError("采样率无效。")
    if src_sr == dst_sr:
        return samples
    if len(samples) < 2:
        return samples
    ratio = dst_sr / float(src_sr)
    n_out = max(1, int(len(samples) * ratio))
    out = array("h")
    out.extend([0] * n_out)
    inv = 1.0 / ratio
    for i in range(n_out):
        pos = i * inv
        j = int(pos)
        frac = pos - j
        if j >= len(samples) - 1:
            v = int(samples[-1])
        else:
            v0 = int(samples[j])
            v1 = int(samples[j + 1])
            v = int(v0 * (1.0 - frac) + v1 * frac)
        if v > 32767:
            v = 32767
        elif v < -32768:
            v = -32768
        out[i] = v
    return out


def load_wav_as_pcm16_mono(path: str, target_sr: int) -> Sample:
    with wave.open(path, "rb") as wf:
        nch = wf.getnchannels()
        sw = wf.getsampwidth()
        sr = wf.getframerate()
        nframes = wf.getnframes()
        frames = wf.readframes(nframes)

    mono = _pcm_bytes_to_int16_mono(frames, sampwidth=sw, nchannels=nch)
    mono = _resample_linear(mono, src_sr=sr, dst_sr=target_sr)
    return Sample(mono, target_sr)


def find_sample_dir(base_dir: str) -> str:
    base_dir = os.path.abspath(base_dir)
    if os.path.isdir(os.path.join(base_dir, "钢琴88键独立音频文件")):
        return os.path.join(base_dir, "钢琴88键独立音频文件")
    return base_dir


def build_note_to_sample_path(sample_dir: str) -> Dict[int, str]:
    mapping: Dict[int, str] = {}
    for midi in range(21, 109):
        name = midi_to_note_name(midi)
        mapping[midi] = os.path.join(sample_dir, f"{name}.wav")
    return mapping


def render(
    spans: List[NoteSpan],
    tpq: int,
    tempos: List[TempoEvent],
    sample_root: str,
    out_wav: str,
    target_sr: int,
    normalize_peak: int,
) -> None:
    tempo_points = build_tempo_map(tpq, tempos)
    sample_dir = find_sample_dir(sample_root)
    note_to_path = build_note_to_sample_path(sample_dir)

    for midi, p in note_to_path.items():
        if not os.path.isfile(p):
            raise FileNotFoundError(f"缺少采样文件：{p}（对应 MIDI={midi}）")

    cache: Dict[int, Sample] = {}
    def get_sample(note: int) -> Sample:
        s = cache.get(note)
        if s is None:
            s = load_wav_as_pcm16_mono(note_to_path[note], target_sr=target_sr)
            cache[note] = s
        return s

    note_events = []
    max_end = 0.0
    for sp in spans:
        if sp.note < 21 or sp.note > 108:
            continue
        if sp.end_tick <= sp.start_tick:
            continue
        st = tick_to_seconds(sp.start_tick, tpq, tempo_points)
        et = tick_to_seconds(sp.end_tick, tpq, tempo_points)
        if et <= st:
            continue
        note_events.append((st, et, sp.note, sp.velocity))
        if et > max_end:
            max_end = et

    note_events.sort(key=lambda x: x[0])
    total_seconds = max_end + 0.25
    total_frames = int(total_seconds * target_sr) + 1
    mix = array("i", [0]) * total_frames

    for st, et, note, vel in note_events:
        start_f = int(st * target_sr)
        dur_f = max(1, int((et - st) * target_sr))
        if start_f >= total_frames:
            continue
        if start_f + dur_f > total_frames:
            dur_f = total_frames - start_f
        if dur_f <= 0:
            continue

        sample = get_sample(note).pcm16_mono
        if len(sample) <= 0:
            continue

        gain = max(0.0, min(1.0, vel / 127.0))
        for i in range(dur_f):
            s = int(sample[i % len(sample)])
            v = int(s * gain)
            mix[start_f + i] += v

    peak = 0
    for v in mix:
        av = -v if v < 0 else v
        if av > peak:
            peak = av

    factor = 1.0
    if peak > 0 and peak > normalize_peak:
        factor = normalize_peak / float(peak)

    out16 = array("h")
    out16.extend([0] * total_frames)
    for i in range(total_frames):
        v = int(mix[i] * factor)
        if v > 32767:
            v = 32767
        elif v < -32768:
            v = -32768
        out16[i] = v

    os.makedirs(os.path.dirname(os.path.abspath(out_wav)), exist_ok=True)
    with wave.open(out_wav, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(target_sr)
        if sys.byteorder != "little":
            out16.byteswap()
        wf.writeframes(out16.tobytes())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("midi_pos", nargs="?", help="输入 .mid/.midi 文件路径（也可用 --midi）")
    parser.add_argument("--midi", default="", help="输入 .mid/.midi 文件路径")
    parser.add_argument(
        "--samples",
        default=r"D:\民族文化大模型\模拟乐器演奏\音频文件",
        help="采样根目录（可直接指向“音频文件”或“钢琴88键独立音频文件”）",
    )
    parser.add_argument("--out", default="", help="输出 wav 路径（默认与 midi 同目录同名 .wav）")
    parser.add_argument("--sr", type=int, default=44100, help="输出采样率")
    parser.add_argument("--normalize", type=int, default=28000, help="峰值归一化目标（<=32767）")
    parser.add_argument("--play", action="store_true", help="合成后直接播放（Windows）")
    args = parser.parse_args()

    midi_in = (args.midi or "").strip() or (args.midi_pos or "").strip()
    if not midi_in:
        print("缺少 MIDI 参数。", file=sys.stderr)
        print(
            '示例：python .\\play_midi_with_piano_samples.py "D:\\path\\to\\song.mid"',
            file=sys.stderr,
        )
        print(
            '或：  python .\\play_midi_with_piano_samples.py --midi "D:\\path\\to\\song.mid"',
            file=sys.stderr,
        )
        return 2

    midi_path = os.path.abspath(midi_in)
    if not os.path.isfile(midi_path):
        print(f"找不到 MIDI 文件：{midi_path}", file=sys.stderr)
        return 2

    out_wav = args.out.strip()
    if not out_wav:
        base, _ = os.path.splitext(midi_path)
        out_wav = base + ".wav"
    out_wav = os.path.abspath(out_wav)

    tpq, tempos, spans = parse_midi(midi_path)
    render(
        spans=spans,
        tpq=tpq,
        tempos=tempos,
        sample_root=args.samples,
        out_wav=out_wav,
        target_sr=int(args.sr),
        normalize_peak=int(args.normalize),
    )

    print(f"已输出：{out_wav}")

    if args.play:
        try:
            import winsound

            winsound.PlaySound(out_wav, winsound.SND_FILENAME)
        except Exception as e:
            print(f"播放失败：{e}", file=sys.stderr)
            return 4

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
