#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
简谱OCR数据集合成器
- 生成白底黑字的“标准格式”简谱（整页谱面 + 可选行级裁剪）
- 随机生成：数字(0-7)、点(.)、下划线(_)、延长线(—)、小节线(|)
- 自动打标签：labels_train.txt / labels_val.txt
"""
import os
import sys
import math
import random
import argparse
import json
from pathlib import Path
from typing import List, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from tqdm import tqdm


# 允许的字符集（训练时需一致）
# 新增休止符 0，高低音点表示，升降号等，以及新的连音线、反复记号等标记
CHARSET = list("01234567._|— #bṾ̇⌒()[]{}:")


def list_font_files(fonts_dir: Path) -> List[Path]:
    """枚举系统/自定义字体文件"""
    exts = {".ttf", ".otf", ".ttc"}
    fonts = []
    if fonts_dir.exists():
        for p in fonts_dir.iterdir():
            if p.suffix.lower() in exts:
                fonts.append(p)
    # 兜底：如果未找到，留空以使用 PIL 默认字体
    return fonts


def random_jianpu_sequence(min_notes: int = 6, max_notes: int = 24) -> str:
    """生成类似'12356| 3— 5_ |'的随机简谱文本（旧版遗留，建议使用 page 模式生成更规范的简谱）"""
    n = random.randint(min_notes, max_notes)
    tokens = []
    for i in range(n):
        # 数字音符及休止符
        digit = random.choice(list("01234567"))
        tok = digit
        # 升降号
        if digit != "0" and random.random() < 0.1:
            tok = random.choice(["#", "b"]) + tok
        # 随机高低音点（表示附点或八度标记，用简化文本点表示）
        if digit != "0" and random.random() < 0.25:
            tok += random.choice([".", "_"])
        # 延长线（用 em-dash 近似）
        if random.random() < 0.15:
            tok += "—"
        tokens.append(tok)
        # 随机空格增加可读性与变形
        if random.random() < 0.4:
            tokens.append(" ")
        # 随机小节线
        if random.random() < 0.18:
            tokens.append("|")
            if random.random() < 0.6:
                tokens.append(" ")
    # 合并，清理多余空格
    s = "".join(tokens).strip()
    # 防止太短
    if len(s) < 4:
        s += " |"
    return s


def rand_choice_weighted(items: List[Tuple[int, float]]) -> int:
    r = random.random()
    acc = 0.0
    for v, w in items:
        acc += w
        if r <= acc:
            return v
    return items[-1][0]


def draw_jianpu_note(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    digit: str,
    font: ImageFont.FreeTypeFont,
    acc_font: ImageFont.FreeTypeFont | None,
    underline_count: int,
    extend_count: int,
    dot_count: int,  # 附点（时值）
    dot_pos: str,    # 高低音点位置 (above, below, above2, below2)
    accidental: str, # 升降号 (#, b, none)
    ink: Tuple[int, int, int],
    gap_y: int | None = None,
):
    w = 0
    h = 0
    bbox = draw.textbbox((x, y), digit, font=font)
    draw.text((x, y), digit, font=font, fill=ink)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    note_bottom = bbox[3]

    # 画升降号（在左侧）
    if accidental in ("#", "b"):
        if acc_font is None:
            acc_font = pil_safe_font(Path(), size=max(10, int(font.size * 0.7)))
        acc_bbox = draw.textbbox((0, 0), accidental, font=acc_font)
        acc_w = acc_bbox[2] - acc_bbox[0]
        # 升降号位置：紧贴数字左侧，垂直居中
        draw.text((x - acc_w - max(1, int(w * 0.15)), y + int(h * 0.15)), accidental, font=acc_font, fill=ink)

    # 画下划线（减时线，单音符情况，画在正下方）
    if underline_count > 0:
        gy = gap_y if gap_y is not None else max(6, int(h * 0.22))
        line_y0 = note_bottom + gy
        gap = gy
        for i in range(underline_count):
            yy = line_y0 + i * gap
            draw.line([(x, yy), (x + w, yy)], fill=ink, width=max(1, int(h * 0.08)))

    # 画延长线（增时线，音符右侧）
    if extend_count > 0:
        # 增时线长度与音符宽度挂钩
        seg_len = max(8, int(w * 0.7))
        # 间距
        gap_x = max(3, int(seg_len * 0.4))
        thick = max(1, int(h * 0.05))
        # 垂直位置：居中对齐音符数字的腰部
        y_mid = y + int(h * 0.55)
        
        # 起始位置：如果有附点，要往后挪；如果有升降号，本身x已经调整过，这里相对bbox算即可
        start_x = x + w + max(2, int(w * 0.2)) 
        if dot_count > 0:
             start_x += (int(w * 0.35))

        for i in range(extend_count):
            x0 = start_x + i * (seg_len + gap_x)
            x1 = x0 + seg_len
            draw.line([(x0, y_mid), (x1, y_mid)], fill=ink, width=thick)

    # 画附点（时值点，在右侧，紧跟音符，但在增时线之前）
    if dot_count > 0:
        r = max(1, int(h * 0.08))
        dot_gap_x = max(2, int(w * 0.18))
        mid_y = (bbox[1] + bbox[3]) // 2
        for i in range(dot_count):
            cx = x + w + dot_gap_x + i * (r * 3)
            cy = mid_y
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=ink, outline=ink)

    # 画高低音点（正上/正下，竖直排列）
    if digit != "0" and dot_pos != "none":
        r = max(1, int(h * 0.08))
        cx = x + int(w / 2)
        gy = gap_y if gap_y is not None else max(6, int(h * 0.22))
        
        points_to_draw = []
        if dot_pos == "above":
            points_to_draw = [y - gy]
        elif dot_pos == "below":
            # 如果有减时线，低音点要画在减时线的最下方
            # 减时线从 line_y0 开始，共有 underline_count 条，间距 gap
            if underline_count > 0:
                line_y0 = note_bottom + gy
                gap = gy
                # 最后一根线的Y坐标
                last_line_y = line_y0 + (underline_count - 1) * gap
                points_to_draw = [last_line_y + gy]
            else:
                points_to_draw = [note_bottom + gy]
        elif dot_pos == "above2": # 双高音点
            points_to_draw = [y - gy, y - 2 * gy]
        elif dot_pos == "below2": # 双低音点
            if underline_count > 0:
                line_y0 = note_bottom + gy
                gap = gy
                last_line_y = line_y0 + (underline_count - 1) * gap
                start_dot_y = last_line_y + gy
                points_to_draw = [start_dot_y, start_dot_y + gy]
            else:
                points_to_draw = [note_bottom + gy, note_bottom + 2 * gy]
            
        for cy in points_to_draw:
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=ink, outline=ink)

    return bbox


def synthesize_jianpu_page(
    page_size: Tuple[int, int],
    fonts: List[Path],
    clean: bool,
    title: str,
    lines_per_page: int,
    measures_per_line: int,
    seed_hint: int,
) -> Tuple[Image.Image, List[Tuple[Image.Image, str]], List[str]]:
    random.seed(seed_hint)

    W, H = page_size
    img = Image.new("RGB", (W, H), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    ink = (0, 0, 0)
    title_font = pil_safe_font(random.choice(fonts) if fonts else Path(), size=int(H * 0.035))
    meta_font = pil_safe_font(random.choice(fonts) if fonts else Path(), size=int(H * 0.02))
    note_font_path = random.choice(fonts) if fonts else Path()
    note_font = pil_safe_font(note_font_path, size=int(H * 0.03))
    acc_scale = random.uniform(0.60, 0.70)
    acc_font = pil_safe_font(note_font_path, size=max(10, int(note_font.size * acc_scale)))

    margin_x = int(W * 0.08)
    left_x = margin_x
    right_x = W - margin_x
    content_w = right_x - left_x

    top = int(H * 0.05)
    header_h = int(H * 0.12)

    t_bbox = draw.textbbox((0, 0), title, font=title_font)
    t_w = t_bbox[2] - t_bbox[0]
    draw.text((max(margin_x, (W - t_w) // 2), top), title, font=title_font, fill=ink)
    meta = random.choice(["1=C  4/4", "1=G  4/4", "1=F  4/4", "1=D  4/4"])
    draw.text((margin_x, top + int(H * 0.055)), meta, font=meta_font, fill=ink)
    tempo = random.choice([60, 72, 80, 92, 100, 120])
    tempo_text = f"♩={tempo}"
    tempo_font = meta_font
    seguisym = Path("C:/Windows/Fonts/seguisym.ttf")
    if seguisym.exists():
        tempo_font = pil_safe_font(seguisym, size=meta_font.size)
    tb = draw.textbbox((0, 0), tempo_text, font=tempo_font)
    tw = tb[2] - tb[0]
    draw.text((right_x - tw, top + int(H * 0.055)), tempo_text, font=tempo_font, fill=ink)

    staff_top = top + header_h
    line_gap = int((H - staff_top - int(H * 0.05)) / lines_per_page)

    line_crops: List[Tuple[Image.Image, str]] = []
    page_lines: List[str] = []

    ref_bbox = draw.textbbox((0, 0), "5", font=note_font)
    ref_w = ref_bbox[2] - ref_bbox[0]
    ref_h = ref_bbox[3] - ref_bbox[1]
    digit_h_max = 0
    digit_min_top = 10**9
    digit_max_bottom = -(10**9)
    for ch in "01234567":
        bb = draw.textbbox((0, 0), ch, font=note_font)
        digit_h_max = max(digit_h_max, bb[3] - bb[1])
        digit_min_top = min(digit_min_top, bb[1])
        digit_max_bottom = max(digit_max_bottom, bb[3])

    tight_gap = max(2, int(ref_w * 0.25))
    beat_gap = max(tight_gap + 2, int(ref_w * 0.7))
    bar_gap = max(beat_gap + 2, int(ref_w * 1.1))

    dot_r_ref = max(1, int(digit_h_max * 0.08))
    x_gap_y = max(12, int(digit_h_max * 0.45), dot_r_ref * 2 + 4)
    x_gap_y = min(x_gap_y, max(12, int(line_gap * 0.22)))
    beam_gap_y = x_gap_y

    def draw_extend_symbol(draw: ImageDraw.ImageDraw, box_left: float, y_note: int, box_w: float) -> None:
        thick = max(1, int(digit_h_max * 0.06))
        seg_len = max(8, int(box_w * 0.55))
        x0 = int(round(box_left + (box_w - seg_len) / 2))
        x1 = x0 + seg_len
        y_mid = y_note + int((digit_min_top + digit_max_bottom) / 2)
        draw.line([(x0, y_mid), (x1, y_mid)], fill=ink, width=thick)

    def note_box_sizes(digit: str, accidental: str, dot_count: int, extend_count: int) -> Tuple[int, int, int]:
        bbox = draw.textbbox((0, 0), digit, font=note_font)
        w = bbox[2] - bbox[0]
        left_extra = 0
        if accidental in ("#", "b"):
            ab = draw.textbbox((0, 0), accidental, font=acc_font)
            aw = ab[2] - ab[0]
            left_extra = aw + max(1, int(w * 0.15))
        dot_extra = 0
        if dot_count > 0:
            r = max(1, int(ref_h * 0.06))
            dot_extra = int(w * 0.15) + dot_count * (r * 3) + r * 2
        ext_extra = 0
        if extend_count > 0:
            seg_len = max(8, int(w * 0.7))
            gap_x = max(3, int(seg_len * 0.4))
            start_pad = max(2, int(w * 0.2)) + (int(w * 0.35) if dot_count > 0 else 0)
            ext_extra = start_pad + extend_count * seg_len + max(0, extend_count - 1) * gap_x
        right_extra = dot_extra + ext_extra
        return left_extra, w, right_extra

    def make_token(note: dict) -> str:
        if note["kind"] == "extend":
            return "—"
        tok = ""
        if note["accidental"] != "none":
            tok += note["accidental"]
        tok += note["digit"]
        if note["dot_pos"] == "above":
            tok += "̇"
        elif note["dot_pos"] == "below":
            tok += "̣"
        elif note["dot_pos"] == "above2":
            tok += "̇̇"
        elif note["dot_pos"] == "below2":
            tok += "̣̣"
        if note["dot_count"] > 0:
            tok += "." * note["dot_count"]
        if note["underline_count"] > 0:
            tok += "_" * note["underline_count"]
        if note.get("tie_to_next", False):
            tok += "⌒"
        if note["has_slur"]:
            tok += "("
        return tok

    def build_note(digit: str, underline_count: int, extend_count: int, dot_count: int, dot_pos: str, accidental: str) -> dict:
        left_extra, digit_w, right_extra = note_box_sizes(digit, accidental, dot_count, extend_count)
        return {
            "kind": "note",
            "digit": digit,
            "underline_count": underline_count,
            "extend_count": extend_count,
            "dot_count": dot_count,
            "dot_pos": dot_pos,
            "accidental": accidental,
            "has_slur": False,
            "tie_from_prev": False,
            "tie_to_next": False,
            "left_extra": left_extra,
            "digit_w": digit_w,
            "digit_h": ref_h,
            "right_extra": right_extra,
            "box_w": left_extra + digit_w + right_extra,
        }

    def build_extend_box() -> dict:
        w = min_note["box_w"]
        return {
            "kind": "extend",
            "digit": "—",
            "underline_count": 0,
            "extend_count": 0,
            "dot_count": 0,
            "dot_pos": "none",
            "accidental": "none",
            "has_slur": False,
            "tie_from_prev": False,
            "tie_to_next": False,
            "left_extra": 0,
            "digit_w": 0,
            "digit_h": ref_h,
            "right_extra": 0,
            "box_w": w,
        }

    def gen_measure_with_state(carry: dict, repeat_open: bool, allow_spill: bool) -> Tuple[dict, dict, bool]:
        beats: List[List[dict]] = []
        beats_count = 4
        b_idx = 0
        while b_idx < beats_count:
            if carry is not None and carry["remaining_beats"] > 0:
                beats_left = beats_count - b_idx
                seg_beats = min(carry["remaining_beats"], beats_left)
                note = build_note(
                    digit=carry["digit"],
                    underline_count=0,
                    extend_count=0,
                    dot_count=0,
                    dot_pos=carry["dot_pos"],
                    accidental=carry["accidental"],
                )
                note["tie_from_prev"] = True
                note["tie_to_next"] = (carry["remaining_beats"] > seg_beats)
                beats.append([note])
                b_idx += 1
                for _ in range(seg_beats - 1):
                    if b_idx < beats_count:
                        beats.append([build_extend_box()])
                        b_idx += 1
                carry["remaining_beats"] -= seg_beats
                if carry["remaining_beats"] <= 0:
                    carry = None
                continue

            rhythm_type = rand_choice_weighted([(0, 0.38), (1, 0.26), (2, 0.10), (3, 0.08), (4, 0.08), (5, 0.05), (6, 0.05)])
            remaining_beats = beats_count - b_idx

            if rhythm_type == 0:
                digit = random.choice(list("01234567"))
                is_rest = (digit == "0")
                if is_rest:
                    dot_pos = "none"
                    accidental = "none"
                else:
                    dot_pos = rand_choice_weighted([
                        ("none", 0.6),
                        ("above", 0.15), ("below", 0.15),
                        ("above2", 0.05), ("below2", 0.05),
                    ])
                    accidental = rand_choice_weighted([("none", 0.9), ("#", 0.05), ("b", 0.05)])

                total_beats = rand_choice_weighted([(1, 0.70), (2, 0.20), (3, 0.07), (4, 0.03)])
                if not allow_spill:
                    total_beats = min(total_beats, remaining_beats)
                seg_beats = min(total_beats, remaining_beats)
                dot_count = rand_choice_weighted([(0, 0.85), (1, 0.15)]) if seg_beats == 1 else 0
                note = build_note(digit=digit, underline_count=0, extend_count=0, dot_count=dot_count, dot_pos=dot_pos, accidental=accidental)
                if total_beats > seg_beats:
                    note["tie_to_next"] = True
                    carry = {"digit": digit, "dot_pos": dot_pos, "accidental": accidental, "remaining_beats": total_beats - seg_beats}
                beats.append([note])
                b_idx += 1
                for _ in range(seg_beats - 1):
                    if b_idx < beats_count:
                        beats.append([build_extend_box()])
                        b_idx += 1
                continue

            if rhythm_type == 1:
                notes_info = [(0.5, 1), (0.5, 1)]
            elif rhythm_type == 2:
                notes_info = [(0.25, 2), (0.25, 2), (0.25, 2), (0.25, 2)]
            elif rhythm_type == 3:
                notes_info = [(0.5, 1), (0.25, 2), (0.25, 2)]
            elif rhythm_type == 4:
                notes_info = [(0.25, 2), (0.25, 2), (0.5, 1)]
            elif rhythm_type == 5:
                notes_info = [(0.75, 1), (0.25, 2)]
            else:
                notes_info = [(0.25, 2), (0.75, 1)]

            beat_notes: List[dict] = []
            for n_idx, (dur, underline_count) in enumerate(notes_info):
                digit = random.choice(list("01234567"))
                is_rest = (digit == "0")
                dot_count = 0
                if is_rest:
                    dot_pos = "none"
                    accidental = "none"
                else:
                    dot_pos = rand_choice_weighted([
                        ("none", 0.75),
                        ("above", 0.10), ("below", 0.10),
                        ("above2", 0.025), ("below2", 0.025),
                    ])
                    accidental = rand_choice_weighted([("none", 0.92), ("#", 0.04), ("b", 0.04)])
                    if underline_count == 1 and dur == 0.75:
                        dot_count = 1
                note = build_note(digit=digit, underline_count=underline_count, extend_count=0, dot_count=dot_count, dot_pos=dot_pos, accidental=accidental)
                beat_notes.append(note)
            beats.append(beat_notes)
            b_idx += 1

        bar_type = "|"
        if repeat_open:
            if random.random() < 0.20:
                bar_type = ":||"
                repeat_open = False
        else:
            if random.random() < 0.08:
                bar_type = "||:"
                repeat_open = True
        has_v = (random.random() < 0.1)
        return {"beats": beats, "bar_type": bar_type, "has_v": has_v}, carry, repeat_open

    def measure_width(measure: dict) -> Tuple[int, List[int]]:
        gaps: List[int] = []
        fixed = 0
        for b_i, beat in enumerate(measure["beats"]):
            for n_i, note in enumerate(beat):
                fixed += note["box_w"]
                if n_i < len(beat) - 1:
                    gaps.append(tight_gap)
            if b_i < len(measure["beats"]) - 1:
                gaps.append(beat_gap)
        gaps.append(bar_gap)
        fixed += bar_box_w
        gaps.append(bar_gap)
        return fixed, gaps

    min_note = build_note(digit="1", underline_count=0, extend_count=0, dot_count=0, dot_pos="none", accidental="none")
    bar_box_w = min_note["box_w"]
    min_measure_w = 4 * min_note["box_w"] + 3 * beat_gap + bar_gap + bar_box_w

    for li in range(lines_per_page):
        y_line = staff_top + li * line_gap
        y_note = y_line + int(line_gap * 0.28)

        measures: List[dict] = []
        fixed_sum = 0
        gaps_sum = 0
        gaps_pool: List[int] = []
        carry_state = None
        repeat_open = False

        tries = 0
        while True:
            remaining_line_space = content_w - (fixed_sum + gaps_sum)
            allow_spill = remaining_line_space > (min_measure_w * 1.15)
            carry_in = None if carry_state is None else dict(carry_state)
            m, carry_out, repeat_out = gen_measure_with_state(carry_in, repeat_open, allow_spill=allow_spill)
            m_fixed, m_gaps = measure_width(m)

            if len(measures) == 0 and (m_fixed + sum(m_gaps)) > content_w and tries < 8:
                tries += 1
                continue

            if len(measures) > 0 and (fixed_sum + gaps_sum + m_fixed + sum(m_gaps)) > content_w:
                break

            measures.append(m)
            fixed_sum += m_fixed
            gaps_sum += sum(m_gaps)
            gaps_pool.extend(m_gaps)
            carry_state = carry_out
            repeat_open = repeat_out
            if content_w - (fixed_sum + gaps_sum) < (4 * (ref_w + tight_gap) + 4 * beat_gap + bar_gap):
                break

        if len(measures) == 0:
            m, carry_state, repeat_open = gen_measure_with_state(None, False, allow_spill=False)
            measures.append(m)
            m_fixed, m_gaps = measure_width(measures[0])
            fixed_sum = m_fixed
            gaps_sum = sum(m_gaps)
            gaps_pool = list(m_gaps)
            carry_state = None
            repeat_open = False

        if carry_state is not None:
            carry_state = None
            for b_i, beat in enumerate(measures[-1]["beats"]):
                for note in beat:
                    note["tie_to_next"] = False

        if repeat_open:
            measures[-1]["bar_type"] = ":||"
            repeat_open = False

        if len(gaps_pool) > 0:
            gaps_sum -= gaps_pool[-1]
            gaps_pool = gaps_pool[:-1]

        scaled_gaps_total = max(0, content_w - fixed_sum)
        base_gaps_total = max(1, gaps_sum)
        gap_scale = scaled_gaps_total / base_gaps_total
        gap_scale = max(0.85, min(3.0, gap_scale))

        x = left_x
        tokens: List[str] = []

        gap_cursor = 0
        tie_pending = None
        for m_i, m in enumerate(measures):
            for b_i, beat in enumerate(m["beats"]):
                drawn_notes = []
                deferred_low_dots = []
                for n_i, note in enumerate(beat):
                    box_left = x
                    digit_x = box_left + note["left_extra"]
                    if note["kind"] == "extend":
                        draw_extend_symbol(draw=draw, box_left=box_left, y_note=y_note, box_w=note["box_w"])
                        bbox = (int(box_left), y_note, int(box_left + note["box_w"]), y_note + digit_h_max)
                        center_x = int(box_left + note["box_w"] / 2)
                    else:
                        dot_pos_for_draw = note["dot_pos"]
                        if note["underline_count"] > 0 and note["dot_pos"] in ("below", "below2"):
                            dot_pos_for_draw = "none"
                        bbox = draw_jianpu_note(
                            draw=draw,
                            x=int(digit_x),
                            y=y_note,
                            digit=note["digit"],
                            font=note_font,
                            acc_font=acc_font,
                            underline_count=0,
                            extend_count=note["extend_count"],
                            dot_count=note["dot_count"],
                            dot_pos=dot_pos_for_draw,
                            accidental=note["accidental"],
                            ink=ink,
                            gap_y=x_gap_y,
                        )
                        center_x = int(digit_x) + (bbox[2] - bbox[0]) // 2
                    if note.get("tie_from_prev", False) and tie_pending is not None:
                        x1 = tie_pending["x"]
                        x2 = center_x
                        y1 = y_note - int(digit_h_max * 0.22)
                        y2 = y1
                        dot_r = max(1, int(digit_h_max * 0.08))
                        dot_margin = max(2, int(dot_r * 0.8))
                        if str(tie_pending["dot_pos"]).startswith("above") or str(note["dot_pos"]).startswith("above"):
                            y1 = min(y1, y_note - (x_gap_y + dot_r + dot_margin))
                            y2 = y1
                        arc_h = int(digit_h_max * 0.42)
                        bbox_arc = [min(x1, x2), y1 - arc_h, max(x1, x2), y1 + arc_h]
                        draw.arc(bbox_arc, start=180, end=0, fill=ink, width=max(1, int(digit_h_max * 0.05)))
                        tie_pending = None
                    drawn_notes.append({
                        "digit_x": int(digit_x),
                        "digit_w": bbox[2] - bbox[0],
                        "underline_count": note["underline_count"],
                        "dot_pos": note["dot_pos"],
                        "has_slur": note["has_slur"],
                    })
                    if note["underline_count"] > 0 and note["dot_pos"] in ("below", "below2"):
                        deferred_low_dots.append({
                            "digit_x": int(digit_x),
                            "digit_w": bbox[2] - bbox[0],
                            "dot_pos": note["dot_pos"],
                            "underline_count": note["underline_count"],
                        })
                    tokens.append(make_token(note))
                    x += note["box_w"]
                    if n_i < len(beat) - 1:
                        g = gaps_pool[gap_cursor]
                        gap_cursor += 1
                        x += g * gap_scale
                    if note.get("tie_to_next", False):
                        tie_pending = {"x": center_x, "dot_pos": note["dot_pos"]}
                if b_i < len(m["beats"]) - 1:
                    g = gaps_pool[gap_cursor]
                    gap_cursor += 1
                    x += g * gap_scale

                if len(drawn_notes) > 0:
                    base_y = y_note + digit_max_bottom + x_gap_y
                    beam1_y = base_y
                    beam2_y = base_y + beam_gap_y
                    beam_w = max(1, int(digit_h_max * 0.08))
                    level1 = [n for n in drawn_notes if n["underline_count"] >= 1]
                    if len(level1) > 0:
                        x_start = level1[0]["digit_x"]
                        x_end = level1[-1]["digit_x"] + level1[-1]["digit_w"]
                        draw.line([(x_start, beam1_y), (x_end, beam1_y)], fill=ink, width=beam_w)
                    level2 = [n for n in drawn_notes if n["underline_count"] >= 2]
                    if len(level2) > 0:
                        x_start = level2[0]["digit_x"]
                        x_end = level2[-1]["digit_x"] + level2[-1]["digit_w"]
                        draw.line([(x_start, beam2_y), (x_end, beam2_y)], fill=ink, width=beam_w)

                    if len(deferred_low_dots) > 0:
                        r = max(1, int(digit_h_max * 0.08))
                        for dn in deferred_low_dots:
                            cx = dn["digit_x"] + dn["digit_w"] // 2
                            last_line_y = beam1_y + (dn["underline_count"] - 1) * beam_gap_y
                            y0 = last_line_y + x_gap_y
                            draw.ellipse([cx - r, y0 - r, cx + r, y0 + r], fill=ink, outline=ink)
                            if dn["dot_pos"] == "below2":
                                y1 = y0 + x_gap_y
                                draw.ellipse([cx - r, y1 - r, cx + r, y1 + r], fill=ink, outline=ink)

                    for i in range(len(drawn_notes) - 1):
                        if drawn_notes[i]["has_slur"]:
                            x1 = drawn_notes[i]["digit_x"] + drawn_notes[i]["digit_w"] // 2
                            x2 = drawn_notes[i + 1]["digit_x"] + drawn_notes[i + 1]["digit_w"] // 2
                            y1 = y_note - int(digit_h_max * 0.25)
                            y2 = y1
                            dot_r = max(1, int(digit_h_max * 0.08))
                            dot_margin = max(2, int(dot_r * 0.8))
                            if str(drawn_notes[i]["dot_pos"]).startswith("above") or str(drawn_notes[i + 1]["dot_pos"]).startswith("above"):
                                y1 = min(y1, y_note - (x_gap_y + dot_r + dot_margin))
                                y2 = y1
                            arc_h = int(digit_h_max * 0.45)
                            bbox_arc = [x1, min(y1, y2) - arc_h, x2, max(y1, y2) + arc_h]
                            draw.arc(bbox_arc, start=180, end=0, fill=ink, width=max(1, int(digit_h_max * 0.05)))

            g = gaps_pool[gap_cursor]
            gap_cursor += 1
            x += g * gap_scale

            bar_center = x + (bar_box_w / 2)
            x_bar = int(round(bar_center))
            if m_i == len(measures) - 1:
                x_bar = right_x - int(round(bar_box_w / 2))
            x = x + bar_box_w

            if gap_cursor < len(gaps_pool):
                g = gaps_pool[gap_cursor]
                gap_cursor += 1
                x += g * gap_scale

            bar_top = y_note - int(ref_h * 0.12)
            bar_bot = y_note + ref_h + int(ref_h * 0.18)

            if m["bar_type"] == ":||":
                draw.line([(x_bar - 4, bar_top), (x_bar - 4, bar_bot)], fill=ink, width=1)
                draw.line([(x_bar, bar_top), (x_bar, bar_bot)], fill=ink, width=3)
                dot_r = 2
                draw.ellipse([x_bar - 10, bar_top + (bar_bot - bar_top) * 0.4, x_bar - 10 + dot_r * 2, bar_top + (bar_bot - bar_top) * 0.4 + dot_r * 2], fill=ink)
                draw.ellipse([x_bar - 10, bar_top + (bar_bot - bar_top) * 0.6, x_bar - 10 + dot_r * 2, bar_top + (bar_bot - bar_top) * 0.6 + dot_r * 2], fill=ink)
                tokens.append(":||")
            elif m["bar_type"] == "||:":
                draw.line([(x_bar, bar_top), (x_bar, bar_bot)], fill=ink, width=3)
                draw.line([(x_bar + 4, bar_top), (x_bar + 4, bar_bot)], fill=ink, width=1)
                dot_r = 2
                draw.ellipse([x_bar + 8, bar_top + (bar_bot - bar_top) * 0.4, x_bar + 8 + dot_r * 2, bar_top + (bar_bot - bar_top) * 0.4 + dot_r * 2], fill=ink)
                draw.ellipse([x_bar + 8, bar_top + (bar_bot - bar_top) * 0.6, x_bar + 8 + dot_r * 2, bar_top + (bar_bot - bar_top) * 0.6 + dot_r * 2], fill=ink)
                tokens.append("||:")
            else:
                if m["has_v"]:
                    v_font = pil_safe_font(Path(), size=int(H * 0.02))
                    draw.text((x_bar - int(ref_w * 0.5), bar_top - int(line_gap * 0.2)), "V", font=v_font, fill=ink)
                    tokens.append("V")
                draw.line([(x_bar, bar_top), (x_bar, bar_bot)], fill=ink, width=2)
                tokens.append("|")

        line_text = " ".join(tokens).strip()
        page_lines.append(line_text)

        pad_y = int(line_gap * 0.1)
        crop_box = (
            margin_x,
            max(0, y_line + int(line_gap * 0.08) - pad_y),
            min(W, W - margin_x + 3),
            min(H, y_line + int(line_gap * 0.90) + pad_y),
        )
        line_img = img.crop(crop_box)
        line_crops.append((line_img, line_text))

    if not clean:
        img = add_noise_and_blur(img)
        aug_line_crops = []
        for li_img, li_text in line_crops:
            aug_line_crops.append((add_noise_and_blur(li_img), li_text))
        line_crops = aug_line_crops

    return img, line_crops, page_lines


def pil_safe_font(font_path: Path, size: int) -> ImageFont.FreeTypeFont:
    """尝试加载字体，不可用时使用默认字体"""
    try:
        return ImageFont.truetype(str(font_path), size=size)
    except Exception:
        return ImageFont.load_default()


def render_text_to_image(
    text: str,
    canvas_size: Tuple[int, int],
    font_paths: List[Path],
    min_font: int = 24,
    max_font: int = 64,
) -> Image.Image:
    """在透明层上渲染文本并返回图像（后续再做仿射/噪声）"""
    W, H = canvas_size
    # 随机字体与字号
    size = random.randint(min_font, max_font)
    font = pil_safe_font(random.choice(font_paths) if font_paths else Path(), size)
    # 计算文本大小，必要时缩小字号适配宽度
    temp_img = Image.new("RGBA", (W, H), (255, 255, 255, 0))
    temp_draw = ImageDraw.Draw(temp_img)
    bbox = temp_draw.textbbox((0, 0), text, font=font)
    text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    attempts = 0
    while text_w > int(W * 0.9) and attempts < 5 and size > min_font:
        size = max(min_font, int(size * 0.85))
        font = pil_safe_font(random.choice(font_paths) if font_paths else Path(), size)
        bbox = temp_draw.textbbox((0, 0), text, font=font)
        text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        attempts += 1

    # 在透明图层上居中/随机偏移
    text_layer = Image.new("RGBA", (text_w + 8, text_h + 8), (255, 255, 255, 0))
    draw = ImageDraw.Draw(text_layer)
    # 字色随机深色
    base_color = random.randint(0, 60)
    color = (base_color, base_color, base_color, 255)
    draw.text((4, 4), text, font=font, fill=color)

    # 随机轻微旋转
    angle = random.uniform(-8, 8)
    text_layer = text_layer.rotate(angle, resample=Image.BICUBIC, expand=True)

    # 随机仿射（微小平移/缩放/剪切）
    def affine_params():
        sx = 1.0 + random.uniform(-0.03, 0.03)
        sy = 1.0 + random.uniform(-0.03, 0.03)
        shear = random.uniform(-0.05, 0.05)
        return sx, sy, shear

    sx, sy, shear = affine_params()
    w, h = text_layer.size
    affine_matrix = (sx, shear, 0, shear, sy, 0)
    text_layer = text_layer.transform(
        (w, h), Image.AFFINE, affine_matrix, resample=Image.BICUBIC
    )

    # 合成到白底画布
    canvas = Image.new("RGB", (W, H), (255, 255, 255))
    max_x = max(1, W - text_layer.size[0] - 4)
    max_y = max(1, H - text_layer.size[1] - 4)
    ox = random.randint(2, max(2, max_x))
    oy = random.randint(2, max(2, max_y))
    canvas.paste(text_layer.convert("RGB"), (ox, oy))
    return canvas


def add_noise_and_blur(img: Image.Image) -> Image.Image:
    """添加高斯噪声、椒盐噪声、模糊与亮度对比度扰动"""
    arr = np.array(img).astype(np.float32)
    # 高斯噪声
    if random.random() < 0.9:
        sigma = random.uniform(2, 8)
        noise = np.random.normal(0, sigma, arr.shape).astype(np.float32)
        arr = arr + noise
    # 椒盐噪声
    if random.random() < 0.3:
        s_vs_p = 0.5
        amount = random.uniform(0.005, 0.02)
        num_salt = int(np.ceil(amount * arr.size * s_vs_p))
        num_pepper = int(np.ceil(amount * arr.size * (1.0 - s_vs_p)))
        coords = [np.random.randint(0, i - 1, num_salt) for i in arr.shape]
        arr[coords[0], coords[1], :] = 255
        coords = [np.random.randint(0, i - 1, num_pepper) for i in arr.shape]
        arr[coords[0], coords[1], :] = 0
    # 对比度和亮度
    if random.random() < 0.8:
        alpha = random.uniform(0.9, 1.1)  # 对比度
        beta = random.uniform(-10, 10)    # 亮度
        arr = alpha * arr + beta
    arr = np.clip(arr, 0, 255).astype(np.uint8)
    out = Image.fromarray(arr)
    # 模糊
    if random.random() < 0.6:
        radius = random.uniform(0.3, 1.5)
        out = out.filter(ImageFilter.GaussianBlur(radius))
    # 添加背景纹理线条
    if random.random() < 0.25:
        d = ImageDraw.Draw(out)
        for _ in range(random.randint(1, 3)):
            y = random.randint(5, out.size[1] - 5)
            color = random.randint(200, 240)
            d.line([(0, y), (out.size[0], y)], fill=(color, color, color), width=random.randint(1, 2))
    return out


def save_image(img: Image.Image, out_path: Path, fmt: str):
    """保存图像，JPEG使用随机质量以模拟压缩"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if fmt.lower() in ("jpg", "jpeg"):
        quality = random.randint(65, 95)
        img.save(out_path, format="JPEG", quality=quality, subsampling=2, optimize=True)
    elif fmt.lower() == "webp":
        quality = random.randint(65, 95)
        img.save(out_path, format="WEBP", quality=quality, method=6)
    else:
        img.save(out_path, format="PNG", optimize=True)


def write_label(label_file: Path, fname: str, text: str):
    """追加写入标签文件：<filename>\t<label>"""
    with label_file.open("a", encoding="utf-8") as f:
        f.write(f"{fname}\t{text}\n")


def write_jsonl(jsonl_file: Path, obj: dict):
    jsonl_file.parent.mkdir(parents=True, exist_ok=True)
    with jsonl_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def ensure_charset_file(out_dir: Path):
    """保存charset.txt，训练时加载确保一致"""
    charset_path = out_dir / "charset.txt"
    with charset_path.open("w", encoding="utf-8") as f:
        f.write("".join(CHARSET))


def generate_dataset(
    out_dir: Path,
    num_images: int,
    img_w: int,
    img_h: int,
    fonts_dir: Path,
    fmt: str = "png",
    train_ratio: float = 0.95,
    seed: int = 42,
    mode: str = "page",
    clean: bool = True,
):
    random.seed(seed)
    np.random.seed(seed)

    fonts = list_font_files(fonts_dir)
    root_dir = out_dir / mode
    train_dir = root_dir / "train" / "images"
    val_dir = root_dir / "val" / "images"
    train_label = root_dir / "train" / "labels_train.txt"
    val_label = root_dir / "val" / "labels_val.txt"
    train_jsonl = root_dir / "train" / "labels_train.jsonl"
    val_jsonl = root_dir / "val" / "labels_val.jsonl"
    train_dir.mkdir(parents=True, exist_ok=True)
    val_dir.mkdir(parents=True, exist_ok=True)
    # 清空旧标签
    if mode == "line":
        if train_label.exists():
            train_label.unlink()
        if val_label.exists():
            val_label.unlink()
    else:
        if train_jsonl.exists():
            train_jsonl.unlink()
        if val_jsonl.exists():
            val_jsonl.unlink()

    ensure_charset_file(out_dir)

    for idx in tqdm(range(num_images), desc="Synthesizing", mininterval=0.5):
        if mode == "line":
            text = random_jianpu_sequence()
            img = render_text_to_image(text, (img_w, img_h), fonts)
            if not clean:
                img = add_noise_and_blur(img)
        else:
            title = random.choice(["简谱练习", "练习曲", "旋律片段", "简谱示例", "民歌片段"])
            img, line_crops, page_lines = synthesize_jianpu_page(
                page_size=(img_w, img_h),
                fonts=fonts,
                clean=clean,
                title=title,
                lines_per_page=6,
                measures_per_line=4,
                seed_hint=seed + idx,
            )

        # 划分到 train/val
        is_train = random.random() < train_ratio
        if is_train:
            fname = f"{idx:08d}.{fmt}"
            out_path = train_dir / fname
            save_image(img, out_path, fmt)
            if mode == "line":
                write_label(train_label, fname, text)
            else:
                write_jsonl(train_jsonl, {"file": fname, "lines": page_lines})
        else:
            fname = f"{idx:08d}.{fmt}"
            out_path = val_dir / fname
            save_image(img, out_path, fmt)
            if mode == "line":
                write_label(val_label, fname, text)
            else:
                write_jsonl(val_jsonl, {"file": fname, "lines": page_lines})

        if mode == "page":
            line_root = out_dir / "line_from_page"
            line_train_dir = line_root / "train" / "images"
            line_val_dir = line_root / "val" / "images"
            line_train_label = line_root / "train" / "labels_train.txt"
            line_val_label = line_root / "val" / "labels_val.txt"
            line_train_dir.mkdir(parents=True, exist_ok=True)
            line_val_dir.mkdir(parents=True, exist_ok=True)
            if idx == 0:
                if line_train_label.exists():
                    line_train_label.unlink()
                if line_val_label.exists():
                    line_val_label.unlink()

            for li, (li_img, li_text) in enumerate(line_crops):
                li_name = f"{idx:08d}_{li:02d}.{fmt}"
                if is_train:
                    li_path = line_train_dir / li_name
                    save_image(li_img, li_path, fmt)
                    write_label(line_train_label, li_name, li_text)
                else:
                    li_path = line_val_dir / li_name
                    save_image(li_img, li_path, fmt)
                    write_label(line_val_label, li_name, li_text)


def parse_args():
    parser = argparse.ArgumentParser(description="简谱OCR数据集合成器")
    parser.add_argument("--out_dir", type=str, default="dataset",
                        help="输出数据集根目录")
    parser.add_argument("--num_images", type=int, default=50000,
                        help="生成图像数量（可设到100万，注意磁盘/时间）")
    parser.add_argument("--img_w", type=int, default=1024, help="画布宽度")
    parser.add_argument("--img_h", type=int, default=128, help="画布高度")
    parser.add_argument("--fmt", type=str, default="png",
                        choices=["png", "jpg", "jpeg", "webp"], help="图像格式")
    parser.add_argument("--mode", type=str, default="page",
                        choices=["page", "line"], help="page=整页简谱谱面，line=单行简谱")
    parser.add_argument("--augment", action="store_true",
                        help="启用噪声/模糊等增强（默认关闭，生成干净白底黑字）")
    # Windows系统字体目录默认值
    default_fonts = Path("C:/Windows/Fonts")
    parser.add_argument("--fonts_dir", type=str, default=str(default_fonts),
                        help="字体目录（默认Windows系统字体）")
    parser.add_argument("--seed", type=int, default=42, help="随机种子")
    parser.add_argument("--train_ratio", type=float, default=0.95,
                        help="训练集比例（为确保样本全部进 train，可设为 1.0）")
    args = parser.parse_args()
    args.clean = not args.augment
    return args


def main():
    args = parse_args()
    out_dir = Path(args.out_dir)
    fonts_dir = Path(args.fonts_dir)
    generate_dataset(
        out_dir=out_dir,
        num_images=args.num_images,
        img_w=args.img_w,
        img_h=args.img_h,
        fonts_dir=fonts_dir,
        fmt=args.fmt,
        train_ratio=args.train_ratio,
        seed=args.seed,
        mode=args.mode,
        clean=args.clean,
    )
    if args.mode == "line":
        print("Done. Train labels:", out_dir / args.mode / "train" / "labels_train.txt")
        print("Done. Val labels:", out_dir / args.mode / "val" / "labels_val.txt")
    else:
        print("Done. Train labels:", out_dir / args.mode / "train" / "labels_train.jsonl")
        print("Done. Val labels:", out_dir / args.mode / "val" / "labels_val.jsonl")
    if args.mode == "page":
        print("Also wrote line crops:", out_dir / "line_from_page")
    print("Charset:", out_dir / "charset.txt")


if __name__ == "__main__":
    main()

