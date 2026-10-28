"""Работа с медиа: скачивание по ссылке, проверка формата, перекодирование, извлечение звука, превью."""
import json
import shutil
import subprocess
from pathlib import Path

# Что браузер гарантированно проигрывает без перекодирования.
PLAYABLE_CONTAINERS = {"mov,mp4,m4a,3gp,3g2,mj2", "matroska,webm"}
PLAYABLE_VIDEO = {"h264", "vp8", "vp9", "av1"}
PLAYABLE_AUDIO = {"aac", "mp3", "opus", "vorbis"}

_CREATE_NO_WINDOW = 0x08000000 if hasattr(subprocess, "STARTUPINFO") else 0


class MediaError(RuntimeError):
    pass


def _run(cmd: list[str], timeout: float | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout, creationflags=_CREATE_NO_WINDOW)
    except FileNotFoundError as e:
        raise MediaError(f"Не найдена программа {cmd[0]} — установите ffmpeg и добавьте его в PATH") from e


def check_tools() -> dict:
    return {"ffmpeg": shutil.which("ffmpeg") is not None, "ffprobe": shutil.which("ffprobe") is not None}


def probe(path: Path) -> dict:
    r = _run(["ffprobe", "-v", "error", "-show_entries",
              "format=duration,format_name:stream=codec_type,codec_name,width,height",
              "-of", "json", str(path)], timeout=60)
    if r.returncode != 0:
        raise MediaError(f"Файл не распознан как видео или аудио: {r.stderr.strip()[:300]}")
    info = json.loads(r.stdout or "{}")
    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video" and s.get("codec_name") not in ("mjpeg", "png")), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = info.get("format", {})
    if audio is None:
        raise MediaError("В файле нет звуковой дорожки — распознавать нечего")
    return {
        "duration": float(fmt.get("duration") or 0),
        "container": fmt.get("format_name", ""),
        "vcodec": video.get("codec_name") if video else None,
        "acodec": audio.get("codec_name"),
        "has_video": video is not None,
    }


def is_playable(info: dict) -> bool:
    if info["container"] not in PLAYABLE_CONTAINERS:
        return False
    if info["has_video"] and info["vcodec"] not in PLAYABLE_VIDEO:
        return False
    return info["acodec"] in PLAYABLE_AUDIO


def _has_nvenc() -> bool:
    r = _run(["ffmpeg", "-hide_banner", "-encoders"], timeout=30)
    return "h264_nvenc" in r.stdout


def transcode(src: Path, dst: Path, has_video: bool) -> None:
    """Перекодирует в MP4 (H.264 + AAC). На видеокарте NVIDIA — через NVENC, это в разы быстрее."""
    if has_video:
        vcodec = ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "26"] if _has_nvenc() else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "24"]
        cmd = ["ffmpeg", "-y", "-i", str(src), *vcodec, "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
               "-movflags", "+faststart", str(dst)]
    else:
        cmd = ["ffmpeg", "-y", "-i", str(src), "-vn", "-c:a", "aac", "-b:a", "128k", str(dst)]
    r = _run(cmd)
    if r.returncode != 0:
        raise MediaError(f"Не удалось перекодировать файл: {r.stderr.strip()[-300:]}")


def extract_audio(src: Path, dst: Path) -> None:
    """Моно 16 кГц — формат, на котором обучен Whisper."""
    r = _run(["ffmpeg", "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(dst)])
    if r.returncode != 0:
        raise MediaError(f"Не удалось извлечь звук: {r.stderr.strip()[-300:]}")


def thumbnail(src: Path, dst: Path, duration: float) -> bool:
    t = max(0.0, min(duration * 0.1, 60.0))
    r = _run(["ffmpeg", "-y", "-ss", f"{t:.1f}", "-i", str(src), "-frames:v", "1", "-vf", "scale=480:-2",
              "-q:v", "4", str(dst)], timeout=60)
    return r.returncode == 0 and dst.exists()


def download_url(url: str, out_dir: Path, progress=None) -> tuple[Path, str]:
    """Скачивает видео с YouTube, Vimeo, RuTube и других сайтов (yt-dlp). Возвращает путь и название."""
    import yt_dlp

    def hook(d):
        if progress and d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            if total:
                progress(d.get("downloaded_bytes", 0) / total)

    opts = {
        "format": "bv*[height<=720][ext=mp4]+ba[ext=m4a]/b[height<=720][ext=mp4]/bv*[height<=720]+ba/b",
        "merge_output_format": "mp4",
        "outtmpl": str(out_dir / "source.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [hook],
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as e:  # yt-dlp бросает свои типы ошибок, пользователю важен текст
        if "confirm you" in str(e) and "bot" in str(e):
            raise MediaError("YouTube просит войти в аккаунт, чтобы отдать это видео (защита от ботов). "
                             "Загрузите файл вручную или дайте ссылку на Rutube/VK Видео.") from e
        raise MediaError(f"Не удалось скачать видео по ссылке: {e}") from e
    files = sorted(out_dir.glob("source.*"), key=lambda p: p.stat().st_size, reverse=True)
    files = [f for f in files if f.suffix not in (".part", ".ytdl")]
    if not files:
        raise MediaError("Видео по ссылке не скачалось")
    return files[0], (info or {}).get("title") or url
