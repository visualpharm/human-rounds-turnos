#!/usr/bin/env python3
"""One study video out of usability_kit.fallible_participant runs, one chapter per task.

    python3 -m usability_kit.compile_study STUDY.json [--out study.mp4]

Each chapter: a task card read aloud by a moderator voice, the eye-tracking
replay with large burned-in subtitles of the think-aloud, and an outcome card
(success or fail, the persona's own comment, how hard it felt), read in the
persona's voice. MP4 chapter markers name each task and its outcome.
"""
import argparse, json, os, re, subprocess, sys
from pathlib import Path

from . import fallible_participant as fp  # noqa: E402

W, H, FPS = 1920, 1080, 25
BG = (22, 24, 28)
FONT = "/System/Library/Fonts/Supplemental/Arial.ttf"
BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
DEVICE = {"desktop": "computadora", "phone": "celular"}
SEQ = {1: "muy difícil", 2: "difícil", 3: "algo difícil", 4: "ni fácil ni difícil", 5: "algo fácil", 6: "fácil", 7: "muy fácil"}


def font(size, bold=False):
    from PIL import ImageFont
    return ImageFont.truetype(BOLD if bold else FONT, size)


def wrap(text, f, width):
    lines, cur = [], ""
    for w in text.split():
        t = f"{cur} {w}".strip()
        if f.getlength(t) <= width: cur = t
        else:
            if cur: lines.append(cur)
            cur = w
    if cur: lines.append(cur)
    return lines


def srt(path):
    out = []
    for blk in Path(path).read_text().strip().split("\n\n"):
        rows = blk.split("\n")
        if len(rows) < 3: continue
        a, b = [sum(float(x) * m for x, m in zip(re.split("[:,]", t.strip())[:3], (3600, 60, 1))) + int(re.split("[:,]", t.strip())[3]) / 1000 for t in rows[1].split("-->")]
        out.append((a, b, " ".join(rows[2:])))
    return out


def card(lines_spec):
    """lines_spec: list of (text, size, bold, color, gap_after) drawn centered."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    blocks = []
    for text, size, bold, color, gap in lines_spec:
        f = font(size, bold)
        rows = wrap(text, f, W - 320)
        blocks.append((rows, f, color, int(size * 1.25), gap))
    total = sum(len(r) * lh + gap for r, _, _, lh, gap in blocks)
    y = (H - total) // 2
    for rows, f, color, lh, gap in blocks:
        for r in rows:
            d.text(((W - f.getlength(r)) / 2, y), r, font=f, fill=color)
            y += lh
        y += gap
    return img


def verdict_card(ok, note, seq, heatmap=None, gaze_y=None, reason=""):
    """The persona's closing words over the heatmap of the screen where it ended."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (W, H), BG)
    if heatmap and Path(heatmap).exists():
        hm = Image.open(heatmap).convert("RGB")
        sc = min((W - 80) / hm.width, (H - 48) / hm.height)
        hm = hm.resize((int(hm.width * sc), int(hm.height * sc)), Image.LANCZOS)
        img.paste(hm, ((W - hm.width) // 2, 24))
    fv, fn, fs = font(72, True), font(42), font(38, True)
    pad, inner = 44, W - 360
    rows = wrap(f"«{note}»", fn, inner) if note else []
    ph = pad + 84 + (16 + 50 if reason else 0) + (24 + len(rows) * 54 if rows else 0) + (22 + 48 if seq else 0) + pad
    px0, px1 = 140, W - 140
    # sit on the half of the screen the eyes spent less time on, so the hot spots stay visible
    if gaze_y is not None and gaze_y > 0.5:
        py0 = 48; py1 = py0 + ph
    else:
        py1 = H - 48; py0 = py1 - ph
    over = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(over).rounded_rectangle([px0, py0, px1, py1], radius=28, fill=(14, 16, 20, 222))
    img = Image.alpha_composite(img.convert("RGBA"), over).convert("RGB")
    d = ImageDraw.Draw(img)
    x, y, r = px0 + pad, py0 + pad, 38
    cx, cy = x + r, y + 42
    color = (46, 170, 90) if ok else (215, 60, 50)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)
    if ok:
        d.line([(cx - 18, cy + 1), (cx - 4, cy + 15), (cx + 20, cy - 14)], fill=(255, 255, 255), width=9, joint="curve")
    else:
        d.line([(cx - 15, cy - 15), (cx + 15, cy + 15)], fill=(255, 255, 255), width=9)
        d.line([(cx - 15, cy + 15), (cx + 15, cy - 15)], fill=(255, 255, 255), width=9)
    d.text((x + 2 * r + 24, y), "Lo logró" if ok else "No lo logró", font=fv, fill=(255, 255, 255))
    y += 84
    if reason:
        y += 16
        d.text((x, y), reason, font=font(40, True), fill=(255, 196, 0))
        y += 50
    if rows:
        y += 24
        for row in rows:
            d.text((x, y), row, font=fn, fill=(232, 235, 241))
            y += 54
    if seq:
        y += 22
        d.text((x, y), f"Le resultó {SEQ.get(int(seq), '')}", font=fs, fill=(196, 202, 212))
    return img


def subtitle(img, text):
    from PIL import ImageDraw
    if not text: return img
    d = ImageDraw.Draw(img)
    size = 46
    rows = wrap(text, font(size), W - 240)
    while len(rows) > 2 and size > 34:
        size -= 4
        rows = wrap(text, font(size), W - 240)
    f = font(size)
    lh = int(size * 1.28)
    y = 880 + (180 - lh * len(rows)) // 2
    for r in rows:
        x = (W - f.getlength(r)) / 2
        d.text((x, y), r, font=f, fill=(255, 255, 255), stroke_width=2, stroke_fill=(0, 0, 0))
        y += lh
    return img


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("study")
    ap.add_argument("--out")
    args = ap.parse_args()
    from PIL import Image
    study = json.loads(Path(args.study).read_text())
    root = Path(os.path.expanduser(study["out"]))
    out = Path(args.out) if args.out else root / "study.mp4"
    tmp = root / ".compile"; tmp.mkdir(exist_ok=True)
    key = fp.openai_key()
    enc = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "21", "-preset", "medium", str(tmp / "video.mp4")], stdin=subprocess.PIPE)
    t, clips, chapters = 0.0, [], []

    def hold(img, seconds):
        nonlocal t
        n = int(round(seconds * FPS))
        raw = img.tobytes()
        for _ in range(n): enc.stdin.write(raw)
        t += n / FPS

    cases = [c for c in study["cases"] if (root / c["id"] / "run.json").exists()]
    for k, case in enumerate(cases, 1):
        run = json.loads((root / case["id"] / "run.json").read_text())
        persona = {**fp.PERSONA_DEFAULTS, **study["personas"][case["persona"]]}
        res = run.get("result") or {}
        ok = res.get("verdict") == "done"
        # A persona can believe it is done without seeing what the task promised; the study can require proof.
        override = ""
        check = case.get("verify")
        if ok and check:
            seen = " ".join(" ".join(s["perceived"]) for s in run["steps"])
            if not any(re.search(p, seen, re.I) for p in check["any"]):
                ok, override = False, check.get("fail_text", "")
        start = t
        # task card
        mod = tmp / f"{case['id']}-task.mp3"
        first = persona["name"].split(",")[0]
        fp.tts(key, f"Tarea {k}. {first}: {case['task']}", "verse", "Sos la moderadora de la prueba: leés la consigna con claridad y calma.", mod)
        dur = max(4.0, fp.duration(mod) + 1.2)
        clips.append((t + 0.4, mod))
        hold(card([(f"Tarea {k}", 64, True, (160, 170, 185), 36), (f"{persona['name']} · {DEVICE[persona['device']]}", 48, True, (255, 255, 255), 44),
                   (case["task"], 54, False, (230, 233, 240), 0)]), dur)
        # replay with burned-in subtitles
        rp = root / case["id"] / "replay.mp4"
        subs = srt(root / case["id"] / "narration.srt")
        vw, vh = [int(x) for x in subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0", str(rp)], text=True).strip().split(",")]
        scale = min((W - 80) / vw, 840 / vh)
        sw, sh = int(vw * scale), int(vh * scale)
        dec = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-i", str(rp), "-f", "rawvideo", "-pix_fmt", "rgb24", "-r", str(FPS), "-"], stdout=subprocess.PIPE)
        clip_a = tmp / f"{case['id']}-replay.m4a"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(rp), "-vn", "-c:a", "copy", str(clip_a)], check=True)
        clips.append((t, clip_a))
        frame_bytes = vw * vh * 3
        i = 0
        while True:
            buf = dec.stdout.read(frame_bytes)
            if len(buf) < frame_bytes: break
            now = i / FPS
            frame = Image.frombytes("RGB", (vw, vh), buf).resize((sw, sh), Image.BILINEAR)
            canvas = Image.new("RGB", (W, H), BG)
            canvas.paste(frame, ((W - sw) // 2, 24))
            say = next((s for a, b, s in subs if a <= now <= b + 0.4), "")
            enc.stdin.write(subtitle(canvas, say).tobytes())
            i += 1
        dec.wait()
        t += i / FPS
        # outcome card
        note = (res.get("note") or "").strip()
        vo = tmp / f"{case['id']}-outcome.mp3"
        spoken = note if override else ("Lo logré. " if ok else "No lo logré. ") + note
        fp.tts(key, spoken, persona["voice"], persona["voice_style"], vo)
        dur = max(5.0, fp.duration(vo) + 1.5)
        clips.append((t + 0.5, vo))
        last = run["steps"][-1] if run["steps"] else None
        gy = None
        if last and last["fixations"]:
            wt = sum(f["ms"] for f in last["fixations"])
            gy = sum(f["y"] * f["ms"] for f in last["fixations"]) / wt / last["viewport"][1]
        hold(verdict_card(ok, note, res.get("seq"), root / case["id"] / "heatmaps" / f"step-{last['n']:02d}.png" if last else None, gy, override), dur)
        chapters.append((start, t, f"{k}. {persona['name'].split(',')[0]}: {case['task'].split('.')[0]} — {'lo logró' if ok else 'no lo logró'}"))
        fp.log(f"chapter {k} {case['id']} {'OK' if ok else 'FAIL'} ends {t:.0f}s")
    enc.stdin.close(); enc.wait()
    meta = tmp / "chapters.txt"
    with meta.open("w") as f:
        f.write(";FFMETADATA1\ntitle=Prueba de usabilidad simulada\n")
        for a, b, title in chapters:
            f.write(f"[CHAPTER]\nTIMEBASE=1/1000\nSTART={int(a * 1000)}\nEND={int(b * 1000)}\ntitle={title.replace('=', '-')}\n")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(tmp / "video.mp4"), "-i", str(meta)]
    for _, p in clips: cmd += ["-i", str(p)]
    parts = "".join(f"[{j + 2}:a]adelay={int(a * 1000)}|{int(a * 1000)}[a{j}];" for j, (a, _) in enumerate(clips))
    mix = "".join(f"[a{j}]" for j in range(len(clips))) + f"amix=inputs={len(clips)}:normalize=0,apad[aout]"
    subprocess.run(cmd + ["-filter_complex", parts + mix, "-map", "0:v", "-map", "[aout]", "-map_metadata", "1", "-map_chapters", "1",
                          "-shortest", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)], check=True)
    (root / "chapters.json").write_text(json.dumps([{"start": round(a, 1), "end": round(b, 1), "title": c} for a, b, c in chapters], ensure_ascii=False, indent=1))
    fp.log(f"study video {out} ({fp.duration(out):.0f} s)")


if __name__ == "__main__":
    main()
