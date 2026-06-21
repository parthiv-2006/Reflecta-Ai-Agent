#!/usr/bin/env python3
"""Render Reflecta's terminal output into demo assets (PNG / GIF / MP4).

This turns a *real* captured ``reflecta run`` transcript into a polished,
animated terminal recording for the README — no external screen recorder
needed. The input is the plain-text output of an actual run (see
``scripts/capture_demo.py``); the colours are reconstructed to match
``reflecta.ui``'s Rich palette.

Usage:
    python scripts/render_demo.py --run docs/_demo/run.txt \
        --triage docs/_demo/triage.txt --out docs

Produces (under ``--out``):
    demo.gif, demo.mp4   — animated run (typed line-by-line)
    demo.png             — static hero screenshot of the full run
    triage.png           — static screenshot of ``reflecta triage``

Requires Pillow and ffmpeg on PATH. Windows fonts (Consolas + Segoe UI
Symbol) are used by default; override with --font / --symbol-font.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# ── palette (GitHub-dark, mirrors reflecta.ui Rich styles) ──────────────────
BG = (13, 17, 23)
BAR = (22, 27, 34)
DEFAULT = (201, 209, 217)
DIM = (110, 118, 129)
CYAN = (56, 189, 248)
GREEN = (63, 185, 80)
RED = (248, 81, 73)
YELLOW = (210, 162, 60)
WHITE = (240, 246, 252)

FONT_SIZE = 20
LINE_H = 30
PAD_X = 26
BAR_H = 46
TOP = BAR_H + 14
CHROME_DOTS = [(237, 106, 94), (245, 191, 79), (98, 197, 84)]

SYMBOLS = {"✓", "✗"}


def load_fonts(font_path: str, bold_path: str, symbol_path: str):
    reg = ImageFont.truetype(font_path, FONT_SIZE)
    bold = ImageFont.truetype(bold_path, FONT_SIZE)
    sym = ImageFont.truetype(symbol_path, FONT_SIZE)
    char_w = reg.getlength("M")
    return reg, bold, sym, char_w


# ── colouriser ──────────────────────────────────────────────────────────────
def colourise(line: str):
    """Return (colours, bolds) per-character arrays for one output line."""
    n = len(line)
    colours = [DEFAULT] * n
    bolds = [False] * n
    stripped = line.strip()

    def paint(rx, colour=None, bold=None):
        for m in re.finditer(rx, line):
            for i in range(m.start(), m.end()):
                if colour is not None:
                    colours[i] = colour
                if bold is not None:
                    bolds[i] = bold

    # whole-line base styles
    if stripped and set(stripped) <= {"─"}:
        paint(r".", DIM)
        return colours, bolds
    if stripped.startswith("Reflecta"):
        paint(r".", DIM)
        paint(r"Reflecta", CYAN, True)
        return colours, bolds

    # step lines (8-space indent): "Label<24>  ✓  note"
    if line.startswith("        ") and stripped:
        paint(r".", DIM)  # labels + notes are dim by default

    # accents applied everywhere
    paint(r"✓", GREEN)
    paint(r"✗", RED)
    paint(r"[—–]", YELLOW)
    paint(r"[→·…]", DIM)
    paint(r"KEPT", GREEN, True)
    paint(r"\+\d+(?:\.\d+)?\s*pp", GREEN)
    paint(r"\[\d+/\d+\]", CYAN, True)
    paint(r"\b\w+\.py\b", WHITE, True)

    # section / summary labels (2-space indent, capitalised word)
    for label in (
        "Baseline",
        "Found",
        "Testability",
        "Running",
        "Triage",
        "Coverage",
        "Tests",
        "Escalations",
        "Stop reason",
        "Report",
        "Would attempt",
        "Would skip",
    ):
        paint(rf"(?<=  ){re.escape(label)}\b", WHITE, True)

    # green-highlight the testable count + per-target KEPT delta tails
    paint(r"(?<=Testability  )\d+", GREEN)
    return colours, bolds


def spans(line: str):
    """Group consecutive same-style chars into (text, colour, bold, symbol)."""
    colours, bolds = colourise(line)
    out, cur, cc, cb = [], "", None, None
    for i, ch in enumerate(line):
        if ch in SYMBOLS:
            if cur:
                out.append((cur, cc, cb, False))
                cur = ""
            out.append((ch, colours[i], False, True))
            cc = cb = None
            continue
        if colours[i] != cc or bolds[i] != cb:
            if cur:
                out.append((cur, cc, cb, False))
            cur, cc, cb = ch, colours[i], bolds[i]
        else:
            cur += ch
    if cur:
        out.append((cur, cc, cb, False))
    return out


# ── rendering ────────────────────────────────────────────────────────────────
def clean(lines: list[str]) -> list[str]:
    """Collapse the wrapped absolute report path into one tidy relative line."""
    out = []
    skip = False
    for i, ln in enumerate(lines):
        if skip:
            skip = False
            continue
        if ln.strip().startswith("Report"):
            out.append("  Report       examples/sample_project/reflecta-report.json")
            if i + 1 < len(lines) and "reflecta-report.json" in lines[i + 1]:
                skip = True
            continue
        out.append(ln.rstrip("\n"))
    return out


def canvas_size(lines, command, char_w):
    cols = max([len(command) + 2] + [len(ln) for ln in lines]) + 2
    rows = len(lines) + 3  # prompt + blank + lines + tail
    w = int(PAD_X * 2 + cols * char_w)
    h = TOP + rows * LINE_H + 16
    return max(w, 760), h


def draw_chrome(d, w, command, reg, bold, sym, char_w):
    d.rectangle([0, 0, w, BAR_H], fill=BAR)
    for i, c in enumerate(CHROME_DOTS):
        cx = PAD_X + i * 26
        d.ellipse([cx, BAR_H // 2 - 7, cx + 14, BAR_H // 2 + 7], fill=c)
    title = "reflecta — auto test generation"
    d.text(
        (w / 2 - len(title) * char_w / 2, BAR_H // 2 - FONT_SIZE // 2),
        title,
        font=reg,
        fill=DIM,
    )


def draw_line(d, y, text, char_w, reg, bold, sym, spans_):
    x = PAD_X
    for txt, colour, is_bold, is_sym in spans_:
        if is_sym:
            d.text((x, y), txt, font=sym, fill=colour or DEFAULT)
            x += char_w
        else:
            f = bold if is_bold else reg
            d.text((x, y), txt, font=f, fill=colour or DEFAULT)
            x += char_w * len(txt)


def render_frame(lines, command, n_visible, fonts, char_w, w, h, cursor=True):
    reg, bold, sym, _ = fonts
    img = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(img)
    draw_chrome(d, w, command, reg, bold, sym, char_w)
    y = TOP
    # prompt
    d.text((PAD_X, y), "$ ", font=bold, fill=GREEN)
    d.text((PAD_X + char_w * 2, y), command, font=reg, fill=WHITE)
    y += LINE_H * 2
    for ln in lines[:n_visible]:
        draw_line(d, y, ln, char_w, reg, bold, sym, spans(ln))
        y += LINE_H
    if cursor and n_visible < len(lines):
        d.rectangle([PAD_X, y + 4, PAD_X + char_w - 2, y + FONT_SIZE], fill=DIM)
    return img


def static_png(lines, command, fonts, char_w, path):
    w, h = canvas_size(lines, command, char_w)
    img = render_frame(lines, command, len(lines), fonts, char_w, w, h, cursor=False)
    img.save(path)
    print("wrote", path)


def animate(lines, command, fonts, char_w, out_dir, prefix):
    w, h = canvas_size(lines, command, char_w)
    tmp = Path(tempfile.mkdtemp(prefix="reflecta_frames_"))
    idx = 0

    def emit(n_vis, holds=1, cursor=True):
        nonlocal idx
        frame = render_frame(lines, command, n_vis, fonts, char_w, w, h, cursor)
        for _ in range(holds):
            frame.save(tmp / f"f{idx:04d}.png")
            idx += 1

    emit(0, holds=6)  # prompt + blinking cursor beat
    for i in range(1, len(lines) + 1):
        last = lines[i - 1].strip()
        holds = 1
        if "Escalating" in last:
            holds = 8  # pause — Claude is "thinking"
        elif "Escalation" in last and "✓" in last:
            holds = 6
        elif last.startswith("Coverage") and "KEPT" in last:
            holds = 3
        emit(i, holds=holds)
    emit(len(lines), holds=28, cursor=False)  # hold final

    mp4 = Path(out_dir) / f"{prefix}.mp4"
    gif = Path(out_dir) / f"{prefix}.gif"
    fps = 10
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-framerate",
            str(fps),
            "-i",
            str(tmp / "f%04d.png"),
            "-vf",
            "pad=ceil(iw/2)*2:ceil(ih/2)*2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(mp4),
        ],
        check=True,
        capture_output=True,
    )
    palette = tmp / "palette.png"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-framerate",
            str(fps),
            "-i",
            str(tmp / "f%04d.png"),
            "-vf",
            "fps=10,scale=820:-1:flags=lanczos,palettegen=stats_mode=full",
            str(palette),
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-framerate",
            str(fps),
            "-i",
            str(tmp / "f%04d.png"),
            "-i",
            str(palette),
            "-lavfi",
            "fps=10,scale=820:-1:flags=lanczos[x];[x][1:v]paletteuse",
            str(gif),
        ],
        check=True,
        capture_output=True,
    )
    shutil.rmtree(tmp, ignore_errors=True)
    print("wrote", mp4, "and", gif)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="captured `reflecta run` text")
    ap.add_argument("--triage", help="captured `reflecta triage` text")
    ap.add_argument("--out", default="docs")
    ap.add_argument(
        "--command", default="reflecta run --path examples/sample_project --escalate"
    )
    ap.add_argument("--font", default="C:/Windows/Fonts/consola.ttf")
    ap.add_argument("--bold-font", default="C:/Windows/Fonts/consolab.ttf")
    ap.add_argument("--symbol-font", default="C:/Windows/Fonts/seguisym.ttf")
    args = ap.parse_args()

    fonts = load_fonts(args.font, args.bold_font, args.symbol_font)
    char_w = fonts[3]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    run_lines = clean(Path(args.run).read_text(encoding="utf-8").splitlines())
    # drop leading/trailing blank lines
    while run_lines and not run_lines[0].strip():
        run_lines.pop(0)
    while run_lines and not run_lines[-1].strip():
        run_lines.pop()

    static_png(run_lines, args.command, fonts, char_w, out / "demo.png")
    animate(run_lines, args.command, fonts, char_w, out, "demo")

    if args.triage:
        tri = clean(Path(args.triage).read_text(encoding="utf-8").splitlines())
        while tri and not tri[0].strip():
            tri.pop(0)
        while tri and not tri[-1].strip():
            tri.pop()
        static_png(
            tri,
            "reflecta triage --path examples/sample_project",
            fonts,
            char_w,
            out / "triage.png",
        )


if __name__ == "__main__":
    main()
