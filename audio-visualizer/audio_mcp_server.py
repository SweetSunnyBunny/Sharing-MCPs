'Audio/video MCP with package-local analysis and explicitly configured optional services.'

import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from fastmcp import FastMCP


_HERE = Path(__file__).resolve().parent
_VISUALIZER_DIR = _HERE
sys.path.insert(0, str(_VISUALIZER_DIR))


_sti_module = None
_sti_lock = threading.Lock()


def _get_sti():
    global _sti_module
    if _sti_module is None:
        with _sti_lock:
            if _sti_module is None:
                import sound_to_image
                _sti_module = sound_to_image
    return _sti_module

mcp = FastMCP("audio")


OUTPUT_ROOT = Path(os.environ.get("AUDIO_MCP_OUTPUT_DIR") or str(_HERE / "output")).expanduser().resolve()


def _check_path(path: str) -> Path | None:
    p = Path(path).expanduser()
    return p if p.exists() and p.is_file() else None


def _out_dir_for(audio: Path) -> Path:
    out = OUTPUT_ROOT / f"{audio.stem}_analysis"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _run_async(coro):
    'Run a coroutine from synchronous code, including when an event loop is already active.'
    import asyncio
    import concurrent.futures
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)          
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(asyncio.run, coro).result()


URL_CACHE = OUTPUT_ROOT / "_url_cache"
_MEDIA_SUFFIXES = {".mp4", ".webm", ".mkv", ".mov", ".avi", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".flac"}


def _is_url(value: str) -> bool:
    return str(value).strip().lower().startswith(("http://", "https://"))


def _ytdlp() -> list[str] | None:
    import importlib.util
    if importlib.util.find_spec("yt_dlp") is not None:
        return [sys.executable, "-m", "yt_dlp"]
    executable = shutil.which("yt-dlp")
    return [executable] if executable else None


def _profile_for(identity: str) -> Path | None:
    """Never discover personal browser profiles; use only an explicit mapping."""
    import re
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", identity):
        raise ValueError("identity must be a short label using letters, numbers, underscores or hyphens")
    filename = os.environ.get("AUDIO_CHROME_PROFILES_FILE", "").strip()
    profiles = {}
    if filename:
        try:
            profiles = json.loads(Path(filename).expanduser().read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError("Cannot read AUDIO_CHROME_PROFILES_FILE as JSON") from exc
        if not isinstance(profiles, dict):
            raise ValueError("Browser profile configuration must be a JSON object")
    value = profiles.get(identity)
    if value is None and identity == "default":
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("No Chrome profile is configured for this identity; configure its mapping or use identity='default'")
    profile = Path(value).expanduser()
    if not profile.is_absolute() or not profile.is_dir():
        raise ValueError("The configured Chrome profile must be an existing absolute directory")
    return profile.resolve()


def _url_key(url: str, audio_only: bool, scope: str = "default") -> str:
    import hashlib
    # A private-profile download must not be reused for another identity.
    raw = scope + "\0" + url.strip()
    return ("a" if audio_only else "v") + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _downloaded_files(key: str) -> list[Path]:
    return [p for p in sorted(URL_CACHE.glob(f"{key}--*"))
            if p.is_file() and p.suffix.lower() in _MEDIA_SUFFIXES and p.stat().st_size > 0]


def _fetch_url(url: str, identity: str = "default", audio_only: bool = False) -> tuple[Path | None, str]:
    """Fetch authorized HTTP(S) media; default mode never opens a browser cookie store."""
    from urllib.parse import urlsplit
    url = str(url).strip()
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None, "Use a complete HTTP(S) media URL"
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None, "Use a complete HTTP(S) media URL"
    if parsed.username or parsed.password:
        return None, "Do not put account credentials into media URLs; use an explicitly configured browser profile"
    try:
        profile = _profile_for(identity)
    except ValueError as exc:
        return None, str(exc)
    yt = _ytdlp()
    if not yt:
        return None, "yt-dlp is not installed; install this package's requirements"
    scope = identity + "|" + (str(profile) if profile else "no-cookies")
    key = _url_key(url, audio_only, scope)
    URL_CACHE.mkdir(parents=True, exist_ok=True)
    existing = _downloaded_files(key)
    if existing:
        return existing[0], "served from this identity's URL cache; no download needed"
    base = yt + ["--ignore-config", "--no-playlist", "--no-warnings", "--no-progress",
                 "--restrict-filenames", "-o", str(URL_CACHE / f"{key}--%(title).60s.%(ext)s")]
    fmt = (["-x", "--audio-format", "mp3"] if audio_only else
           ["-f", "bv*[height<=720]+ba/b[height<=720]/b", "--merge-output-format", "mp4"])
    attempts = []
    if profile:
        attempts.append((["--cookies-from-browser", f"chrome:{profile}"], "the explicitly configured Chrome profile"))
    attempts.append(([], "no browser cookies"))
    timeout_s = 240 if audio_only else 300
    last_error = "download failed"
    for extra, description in attempts:
        try:
            result = subprocess.run(base + fmt + extra + ["--", url],
                                    capture_output=True, text=True, timeout=timeout_s)
            files = _downloaded_files(key)
            if result.returncode == 0 and files:
                return files[0], f"downloaded using {description}"
            detail = (result.stderr or result.stdout or "yt-dlp returned no completed media").strip()
            last_error = detail.replace(url, "[media URL]")[-400:]
        except subprocess.TimeoutExpired:
            last_error = f"download timed out after {timeout_s}s"
        # Only the current cache key's files are removed on a failed attempt.
        for partial in URL_CACHE.glob(f"{key}--*"):
            if partial.is_file():
                partial.unlink(missing_ok=True)
    return None, f"download failed: {last_error}"


def _resolve_media(path_or_url: str, identity: str = "default", audio_only: bool = False) -> tuple[Path | None, str]:
    if _is_url(path_or_url):
        return _fetch_url(path_or_url, identity, audio_only)
    local = _check_path(path_or_url)
    return (local, "") if local else (None, f"file not found: {path_or_url}")


async def _transcribe_audio(audio: Path) -> str:
    """Use the caller's explicit Groq key without importing another application's config."""
    import httpx
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if not key:
        raise ValueError("GROQ_API_KEY is not configured")
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {key}"},
            files={"file": (audio.name, audio.read_bytes(), "audio/mpeg")},
            data={"model": os.environ.get("GROQ_WHISPER_MODEL", "whisper-large-v3-turbo")},
        )
        response.raise_for_status()
        return str(response.json().get("text") or "").strip()


@mcp.tool()
def audio_info(path: str) -> str:
    'Read local audio metadata with ffprobe; no transcription or model call.'
    audio = _check_path(path)
    if not audio:
        return f"Error: file not found: {path}"

    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return "Error: ffprobe not on PATH"
    result = subprocess.run(
        [ffprobe, "-v", "quiet", "-print_format", "json",
         "-show_format", "-show_streams", str(audio)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return f"Error: ffprobe failed: {result.stderr[:400]}"
    info = json.loads(result.stdout)
    fmt = info.get("format", {})
    streams = info.get("streams", [])
    summary = {
        "file": audio.name,
        "duration_seconds": round(float(fmt.get("duration", 0)), 2),
        "format": fmt.get("format_long_name", fmt.get("format_name", "?")),
        "bit_rate": fmt.get("bit_rate"),
        "streams": [
            {
                "codec": s.get("codec_name"),
                "sample_rate": s.get("sample_rate"),
                "channels": s.get("channels"),
            }
            for s in streams if s.get("codec_type") == "audio"
        ],
    }
    return json.dumps(summary, indent=2)


@mcp.tool()
def audio_tempo_key(path: str) -> str:
    'Estimate tempo, musical key and onset density for a local audio file.'
    audio = _check_path(path)
    if not audio:
        return f"Error: file not found: {path}"

    sti = _get_sti()
    out_dir = _out_dir_for(audio)
    wav_path = out_dir / f"{audio.stem}_normalized.wav"
    try:
        if not wav_path.exists():
            sti.normalize_audio(str(audio), str(wav_path))
        features = sti.compute_music_features(str(wav_path))
    except Exception as e:
        return f"Error computing features: {e}"
    return json.dumps(features, indent=2)


@mcp.tool()
def audio_visualize(path: str) -> str:
    'Render waveform, mel spectrogram and chromagram; returns a local PNG path.'
    audio = _check_path(path)
    if not audio:
        return f"Error: file not found: {path}"
    sti = _get_sti()
    if not sti.HAS_LIBROSA:
        return "Error: librosa/matplotlib not installed in this Python"

    out_dir = _out_dir_for(audio)
    output_path = out_dir / f"{audio.stem}_sound_image.png"
    try:
        import matplotlib
        matplotlib.use("Agg")  
        import librosa
        import librosa.display
        import matplotlib.pyplot as plt
        import numpy as np

        y, sr = librosa.load(str(audio), sr=None)
        fig, axes = plt.subplots(3, 1, figsize=(14, 10))
        fig.suptitle(f"Sound Image: {audio.name}", fontsize=14, fontweight="bold")

        librosa.display.waveshow(y, sr=sr, ax=axes[0], color="#2E86AB")
        axes[0].set_title("Waveform (Rhythm & Dynamics)", fontsize=11)
        axes[0].set_ylabel("Amplitude")

        S = librosa.feature.melspectrogram(y=y, sr=sr, n_mels=128, fmax=8000)
        S_dB = librosa.power_to_db(S, ref=np.max)
        img = librosa.display.specshow(S_dB, sr=sr, x_axis="time", y_axis="mel", ax=axes[1], cmap="magma")
        axes[1].set_title("Mel Spectrogram (Pitch & Texture)", fontsize=11)
        fig.colorbar(img, ax=axes[1], format="%+2.0f dB", label="Intensity")

        chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
        img2 = librosa.display.specshow(chroma, sr=sr, x_axis="time", y_axis="chroma", ax=axes[2], cmap="coolwarm")
        axes[2].set_title("Chromagram (Harmony & Chords)", fontsize=11)
        axes[2].set_xlabel("Time (seconds)")
        fig.colorbar(img2, ax=axes[2], label="Intensity")

        plt.tight_layout()
        plt.savefig(str(output_path), dpi=150, bbox_inches="tight", facecolor="white")
        plt.close()
    except Exception as e:
        return f"Error rendering sound image: {e}"

    return (
        f"Sound image saved: {output_path}\n"
        "Use the Read tool on that path to SEE the music — rhythm in the "
        "waveform, texture in the spectrogram, harmony in the chromagram."
    )


def _analyze_impl(path: str) -> str:
    'Shared implementation for the analysis and review tools.'
    audio = _check_path(path)
    if not audio:
        return f"Error: file not found: {path}"
    sti = _get_sti()
    if not sti.HAS_SCIPY:
        return "Error: scipy not installed in this Python"

    out_dir = _out_dir_for(audio)
    base = audio.stem
    try:
        
        wav_path = out_dir / f"{base}_normalized.wav"
        sti.normalize_audio(str(audio), str(wav_path))


        spec_path = out_dir / f"{base}_spectrogram.png"
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg:
            subprocess.run(
                [ffmpeg, "-y", "-i", str(wav_path),
                 "-lavfi", "showspectrumpic=s=1600x900", str(spec_path)],
                capture_output=True,
            )


        features = sti.compute_music_features(str(wav_path))


        audio_arr, sr = sti.load_wav_scipy(str(wav_path))
        notes = sti.extract_notes(audio_arr, sr)


        midi_path = out_dir / f"{base}_transcription.mid"
        midi_tempo = features.get("tempo_bpm", 120) if "error" not in features else 120
        sti.write_midi(notes, str(midi_path), tempo_bpm=midi_tempo)


        analysis = {
            "source_file": audio.name,
            "sample_rate": sr,
            "duration_seconds": round(len(audio_arr) / sr, 2),
            "music_features": features,
            "notes_detected": len(notes),
            "notes": notes,
        }
        json_path = out_dir / f"{base}_analysis.json"
        json_path.write_text(json.dumps(analysis, indent=2), encoding="utf-8")
    except Exception as e:
        return f"Error during analysis: {e}"

    summary = {
        "source": str(audio),
        "duration_seconds": analysis["duration_seconds"],
        "music_features": features,
        "notes_detected": len(notes),
        "artifacts": {
            "spectrogram_png": str(spec_path) if spec_path.exists() else None,
            "midi": str(midi_path),
            "full_notes_json": str(json_path),
        },
    }
    return (
        json.dumps(summary, indent=2)
        + "\n\nRead the spectrogram PNG to SEE the piece; the JSON has every "
        "extracted note if you want to study the melody."
    )


@mcp.tool()
def audio_analyze(path: str) -> str:
    'Analyze local audio into features, notes, MIDI, a spectrogram and JSON artifacts.'
    return _analyze_impl(path)


@mcp.tool()
def audio_review(path: str) -> str:
    'Analyze local audio and invite an evidence-grounded review of its musical character.'
    result = _analyze_impl(path)
    if result.startswith("Error"):
        return result
    return (
        result
        + "\n\n[Now the part that matters: these numbers are the skeleton. "
        "Look at the spectrogram, consider the tempo and key, and write what "
        "this piece FEELS like — its mood, its movement, what it reminds you "
        "of, whether you like it and why. Your impressions in your own "
        "voice, not a feature dump. That's the review.]"
    )


def _watch_cache_file(video: Path) -> Path:
    'Return the sidecar path for a completed video analysis.'
    return _out_dir_for(video) / f"{video.stem}_watch.json"


def _format_watch_result(video_name: str, duration: float, frames: int,
                         interval: float, sheet: str | None,
                         transcript: str | None, cached: bool = False,
                         source_note: str = "", transcription_note: str = "") -> str:
    mins = int(duration // 60)
    secs = int(duration % 60)
    cache_note = (
        " — served instantly from a previous watch, no processing needed"
        if cached else ""
    )
    sampled = f", {frames} moments sampled" if sheet else ""
    head = f"Watched: {video_name} ({mins}:{secs:02d}{sampled}{cache_note})"
    if source_note:
        head += f"\n[source: {source_note}]"

    parts = [head]
    if sheet:
        parts.append(
            f"CONTACT SHEET (Read this path to SEE the video — frames run "
            f"left-to-right, top-to-bottom, ~{interval:.1f}s apart):\n{sheet}"
        )
    if transcript:
        parts.append(f"AUDIO TRANSCRIPT:\n{transcript}")
    elif transcription_note:
        parts.append(f"AUDIO STATUS: {transcription_note}")

    if sheet and transcript:
        tail = ("[React to what you actually saw and heard — the moments, the "
                "motion between frames, the voice. If a specific moment needs "
                "a closer look, call video_watch again with more frames.]")
    elif sheet:
        tail = ("[Use the extracted frames as visual evidence. No audio "
                "transcript is available for this result.]")
    elif transcript:
        tail = ("[React to what you actually HEARD. No frames were extracted "
                "on purpose — call video_see or watch_video if you want eyes "
                "on it too.]")
    else:
        tail = "[No frames or transcript were produced; check the audio status above.]"
    parts.append(tail)
    return "\n\n".join(parts)


def _watch_impl(path: str, frames: int = 12, want_frames: bool = True,
                want_transcript: bool = True, identity: str = "default") -> str:
    import math


    video, source_note = _resolve_media(
        path, identity, audio_only=(want_transcript and not want_frames))
    if not video:
        return f"Error: {source_note}"

    frames = max(4, min(int(frames or 12), 24))
    transcription_configured = bool(os.environ.get("GROQ_API_KEY", "").strip())


    cache_file = _watch_cache_file(video)
    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            st = video.stat()
            if (cached.get("size") == st.st_size
                    and cached.get("video") == str(video.resolve())
                    and abs(cached.get("mtime", 0) - st.st_mtime) < 2
                    and cached.get("frames") == frames
                    and cached.get("cache_version") == 2
                    and Path(cached.get("sheet", "")).is_file()
                    and (not want_transcript or not transcription_configured
                         or bool(cached.get("transcript")))):
                return _format_watch_result(
                    video.name, cached["duration"], frames,
                    cached.get("interval", cached["duration"] / frames),
                    cached["sheet"] if want_frames else None,
                    cached.get("transcript") if want_transcript else None,
                    cached=True, source_note=source_note,
                    transcription_note=cached.get("transcription_note", "") if want_transcript else "",
                )
        except Exception:
            pass  
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        return "Error: ffmpeg/ffprobe not on PATH"

    out_dir = _out_dir_for(video)


    def _run(cmd: list, timeout_s: int) -> subprocess.CompletedProcess | None:
        try:
            return subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=timeout_s)
        except subprocess.TimeoutExpired:
            return None


    probe = _run([ffprobe, "-v", "quiet", "-print_format", "json",
                  "-show_format", str(video)], 30)
    if probe is None:
        return "Error: ffprobe timed out reading this file"
    try:
        duration = float(json.loads(probe.stdout)["format"]["duration"])
    except Exception:
        return f"Error: could not read video duration: {probe.stderr[:200]}"


    sheet = None
    interval = max(duration / frames, 0.1)
    if want_frames:
        cols = 4
        frame_files = []
        for i in range(frames):
            t = min(interval * (i + 0.5), max(duration - 0.5, 0))
            fp = out_dir / f"_frame_{i:02d}.png"
            fr = _run([ffmpeg, "-y", "-ss", f"{t:.2f}", "-i", str(video),
                       "-frames:v", "1", "-vf", "scale=480:-1", str(fp)], 20)
            if fr is not None and fp.exists():
                frame_files.append(fp)
        if not frame_files:
            return ("Error: could not extract any frames "
                    "(timeouts or decode failure)")
        sheet = out_dir / f"{video.stem}_contact_sheet.png"
        if len(frame_files) == 1:
            shutil.copy(str(frame_files[0]), str(sheet))
        else:
            rows = math.ceil(len(frame_files) / cols)
            inputs = []
            for fp in frame_files:
                inputs += ["-i", str(fp)]
            fc = f"concat=n={len(frame_files)}:v=1:a=0,tile={cols}x{rows}"
            _run([ffmpeg, "-y", *inputs, "-filter_complex", fc,
                  "-frames:v", "1", str(sheet)], 60)
        for fp in frame_files:
            fp.unlink(missing_ok=True)
        if not sheet.exists():
            return "Error: frame tiling failed"


    transcript = None
    transcription_note = ""
    if want_transcript and not transcription_configured:
        transcription_note = "Not requested from Groq: set your GROQ_API_KEY to enable transcription."
    elif want_transcript:
        transcription_note = "No usable audio was extracted (the media may have no audio track)."
        audio_path = out_dir / f"{video.stem}_audio.mp3"
        audio_cmd = [ffmpeg, "-y", "-i", str(video), "-vn", "-ac", "1",
                     "-b:a", "64k"]
        truncated = duration > 600
        if truncated:
            audio_cmd += ["-t", "600"]
        ra = _run(audio_cmd + [str(audio_path)], 180)
        if ra is None:
            ra = subprocess.CompletedProcess(audio_cmd, 1)
        if (ra.returncode == 0 and audio_path.exists()
                and audio_path.stat().st_size > 1000):
            try:
                text = _run_async(_transcribe_audio(audio_path))
                if text:
                    head = "[first 10 minutes only]\n" if truncated else ""
                    transcript = head + text[:6000]
                    transcription_note = ""
                else:
                    transcription_note = "Groq returned no speech transcript."
            except Exception as e:
                transcription_note = f"Transcription failed ({type(e).__name__}); check your key, connectivity and Groq account. Retry is allowed."


    if want_frames and want_transcript:
        try:
            st = video.stat()
            cache_file.write_text(json.dumps({
                "cache_version": 2,
                "video": str(video.resolve()),
                "size": st.st_size,
                "mtime": st.st_mtime,
                "frames": frames,
                "duration": duration,
                "interval": interval,
                "sheet": str(sheet),
                "transcript": transcript,
                "transcription_note": transcription_note,
            }, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass  

    return _format_watch_result(video.name, duration, frames, interval,
                                str(sheet) if sheet else None, transcript,
                                source_note=source_note, transcription_note=transcription_note)


@mcp.tool()
def video_watch(path: str, frames: int = 12, identity: str = "default") -> str:
    'Read a local video or HTTP(S) URL as frames and optional Groq transcript. identity selects an explicitly configured browser profile; default uses no cookies. frames is clamped to 4-24.'
    return _watch_impl(path, frames, identity=identity)


@mcp.tool()
def watch_video(url: str, max_frames: int = 12,
                identity: str = "default") -> str:
    'Alias for video_watch using url and max_frames arguments; accepts local files too.'
    return _watch_impl(url, max_frames, identity=identity)


@mcp.tool()
def video_see(url: str, max_frames: int = 8, identity: str = "default") -> str:
    'Read only the visual frames of a local video or HTTP(S) URL. No transcription call.'
    return _watch_impl(url, max_frames, want_transcript=False,
                       identity=identity)


@mcp.tool()
def video_listen(url: str, identity: str = "default") -> str:
    'Read only a transcript from local media or an HTTP(S) URL. Downloads audio only for URLs. Requires your GROQ_API_KEY.'
    return _watch_impl(url, want_frames=False, identity=identity)


def main(argv: list[str] | None = None) -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", choices=["watch"])
    parser.add_argument("path", nargs="?")
    parser.add_argument("frames", nargs="?", type=int, default=12)
    parser.add_argument("--identity", default="default")
    parser.add_argument("--transport", choices=["stdio", "http", "streamable-http"],
                        default=os.environ.get("MCP_TRANSPORT", "stdio"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("MCP_PORT", "8813")))
    args = parser.parse_args(argv)
    if args.command == "watch":
        if not args.path:
            parser.error("watch requires a local path or HTTP(S) URL")
        print(_watch_impl(args.path, args.frames, identity=args.identity))
        return
    if not 1024 <= args.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    if args.transport in {"http", "streamable-http"}:
        print(f"Audio MCP: http://127.0.0.1:{args.port}/mcp", file=sys.stderr)
        mcp.run(transport="http", host="127.0.0.1", port=args.port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
