"""Собирает демо-лекции: озвучка синтезатором речи Windows (голос Irina) + слайды → MP4.
Заодно пишет эталон для оценки: точное время каждого абзаца и границ разделов.

Запуск:  python -m tools.make_demo_lectures
Результат: data/demo/*.mp4, data/demo/truth.json, data/eval/queries.json"""
import json
import subprocess
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.demo_content import LECTURES, QUERIES  # noqa: E402

DEMO = ROOT / "data" / "demo"
EVAL = ROOT / "data" / "eval"
PAUSE_PARAGRAPH = 0.7
PAUSE_SECTION = 1.8

PS_SCRIPT = r"""
param([string]$jsonPath)
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$voice = $s.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -eq 'ru-RU' } | Select-Object -First 1
if ($null -eq $voice) { Write-Error 'Нет русского голоса'; exit 1 }
$s.SelectVoice($voice.VoiceInfo.Name)
$s.Rate = 1
$items = Get-Content $jsonPath -Encoding UTF8 -Raw | ConvertFrom-Json
foreach ($it in $items) {
  $s.SetOutputToWaveFile($it.path)
  $s.Speak($it.text)
}
$s.SetOutputToNull()
"""


def tts(items: list[dict], work: Path) -> None:
    ps1 = work / "tts.ps1"
    ps1.write_text(PS_SCRIPT, encoding="utf-8-sig")
    jpath = work / "tts.json"
    jpath.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1), str(jpath)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"Синтез речи не удался: {r.stderr}")


def slide(path: Path, lecture_title: str, sec_title: str, bullets: list[str], k: int, n: int) -> None:
    from PIL import Image, ImageDraw, ImageFont
    W, H = 1280, 720
    img = Image.new("RGB", (W, H))
    px = img.load()
    for y in range(H):
        for x in range(0, W, 4):
            t = (x / W) * 0.6 + (y / H) * 0.4
            c = (int(14 + 40 * t), int(16 + 10 * t), int(38 + 60 * t))
            for dx in range(4):
                px[x + dx, y] = c
    d = ImageDraw.Draw(img)
    fonts = Path("C:/Windows/Fonts")
    f_small = ImageFont.truetype(str(fonts / "segoeui.ttf"), 26)
    f_big = ImageFont.truetype(str(fonts / "segoeuib.ttf"), 60)
    f_bul = ImageFont.truetype(str(fonts / "segoeui.ttf"), 38)
    d.text((70, 50), lecture_title, font=f_small, fill=(160, 166, 200))
    d.rectangle((70, 200, 76, 280), fill=(124, 92, 255))
    d.text((100, 196), sec_title, font=f_big, fill=(240, 240, 250))
    y = 330
    for b in bullets:
        d.ellipse((104, y + 18, 116, y + 30), fill=(34, 211, 238))
        d.text((136, y), b, font=f_bul, fill=(220, 222, 235))
        y += 64
    d.text((70, H - 70), f"{k} / {n}", font=f_small, fill=(120, 126, 160))
    img.save(path)


def build(lec: dict) -> dict:
    work = DEMO / lec["key"]
    work.mkdir(parents=True, exist_ok=True)
    items = []
    for si, sec in enumerate(lec["sections"]):
        for pi, text in enumerate(sec["paragraphs"]):
            items.append({"path": str(work / f"s{si}_p{pi}.wav"), "text": text, "s": si, "p": pi})
    print(f"Озвучиваю «{lec['title']}»: {len(items)} абзацев…")
    tts([{"path": it["path"], "text": it["text"]} for it in items], work)

    frames = []
    params = None
    t = 0.0
    paragraphs, section_starts = [], []
    prev_section = None
    for it in items:
        with wave.open(it["path"], "rb") as w:
            if params is None:
                params = w.getparams()
            data = w.readframes(w.getnframes())
            dur = w.getnframes() / w.getframerate()
        if prev_section is not None:
            pause = PAUSE_SECTION if it["s"] != prev_section else PAUSE_PARAGRAPH
            frames.append(b"\x00" * int(pause * params.framerate) * params.sampwidth * params.nchannels)
            t += pause
        if it["s"] != prev_section:
            section_starts.append(round(t, 2))
        paragraphs.append({"section": it["s"], "idx": it["p"], "start": round(t, 2), "end": round(t + dur, 2), "text": it["text"]})
        frames.append(data)
        t += dur
        prev_section = it["s"]
    audio = work / "audio.wav"
    with wave.open(str(audio), "wb") as w:
        w.setparams(params)
        w.writeframes(b"".join(frames))
    total = t

    n = len(lec["sections"])
    lines = []
    for si, sec in enumerate(lec["sections"]):
        png = work / f"slide{si}.png"
        slide(png, lec["title"], sec["title"], sec["slide"], si + 1, n)
        end = section_starts[si + 1] if si + 1 < n else total + 0.5
        lines += [f"file '{png.as_posix()}'", f"duration {end - section_starts[si]:.2f}"]
    lines.append(f"file '{(work / f'slide{n - 1}.png').as_posix()}'")
    (work / "slides.txt").write_text("\n".join(lines), encoding="utf-8")
    out = DEMO / f"{lec['key']}.mp4"
    r = subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(work / "slides.txt"), "-i", str(audio),
                        "-vf", "fps=10,format=yuv420p", "-c:v", "libx264", "-tune", "stillimage", "-preset", "veryfast",
                        "-c:a", "aac", "-b:a", "96k", "-shortest", "-movflags", "+faststart", str(out)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"ffmpeg: {r.stderr[-500:]}")
    print(f"  → {out.name}: {total / 60:.1f} мин")
    return {"key": lec["key"], "title": lec["title"], "video": str(out), "duration": round(total, 2),
            "section_starts": section_starts, "section_titles": [s["title"] for s in lec["sections"]],
            "paragraphs": paragraphs}


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # консоль Windows в cp1251
    DEMO.mkdir(parents=True, exist_ok=True)
    EVAL.mkdir(parents=True, exist_ok=True)
    truth = {lec["key"]: build(lec) for lec in LECTURES}
    (DEMO / "truth.json").write_text(json.dumps(truth, ensure_ascii=False, indent=1), encoding="utf-8")
    queries = []
    for q, answers in QUERIES:
        ans = []
        for key, si, pi in answers:
            p = next(x for x in truth[key]["paragraphs"] if x["section"] == si and x["idx"] == pi)
            ans.append({"lecture": truth[key]["title"], "start": p["start"], "end": p["end"]})
        queries.append({"query": q, "answers": ans})
    seg = {truth[k]["title"]: truth[k]["section_starts"] for k in truth}
    asr = [{"lecture": v["title"], "start": 0, "end": v["duration"], "reference": " ".join(p["text"] for p in v["paragraphs"])}
           for v in truth.values()]
    (EVAL / "queries.json").write_text(json.dumps({"queries": queries, "sections": seg, "asr": asr}, ensure_ascii=False, indent=1),
                                       encoding="utf-8")
    print(f"Эталон: {len(queries)} запросов → {EVAL / 'queries.json'}")


if __name__ == "__main__":
    main()
