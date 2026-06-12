"""Audio + Video MCP — give your companion ears and eyes.

A thin MCP server wrapping sound_to_image.py (same folder) so an AI
companion can perceive media through tool calls:

    audio_info(path)       -> ffprobe metadata (cheap, instant)
    audio_tempo_key(path)  -> tempo (BPM), musical key, onset density
    audio_visualize(path)  -> 3-panel sound image (waveform/mel/chroma PNG)
    audio_analyze(path)    -> full pipeline: features + notes + MIDI + PNG
    audio_review(path)     -> analyze + an invitation to write impressions
    video_watch(path)      -> contact sheet of frames (SEE the video) +
                              audio transcript (HEAR it, via Groq Whisper)

Setup:
    pip install -r requirements.txt        (fastmcp, httpx, numpy, scipy,
                                            librosa, matplotlib)
    + ffmpeg/ffprobe on your PATH           (https://ffmpeg.org)

Optional env vars:
    AUDIO_MCP_OUTPUT_DIR  where artifacts go      (default: ./output)
    GROQ_API_KEY          enables video transcripts via Groq Whisper
                          (whisper-large-v3-turbo, ~$0.04/audio-hour;
                          without it, video_watch still returns frames)

Register (Claude Code ~/.claude.json or equivalent):
    "audio": {
      "command": "python",
      "args": ["/path/to/audio-visualizer/audio_mcp_server.py"]
    }

Shared with love from one pack to another. 💛
"""

import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path

from fastmcp import FastMCP

# sound_to_image.py lives in this same folder.
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
import sound_to_image as sti  # noqa: E402

mcp = FastMCP("audio")

OUTPUT_ROOT = Path(os.environ.get("AUDIO_MCP_OUTPUT_DIR", str(_HERE / "output")))


def _check_path(path: str) -> Path | None:
    p = Path(path).expanduser()
    return p if p.exists() and p.is_file() else None


def _out_dir_for(audio: Path) -> Path:
    out = OUTPUT_ROOT / f"{audio.stem}_analysis"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _groq_transcribe(audio_path: Path) -> str | None:
    """Transcribe via Groq Whisper if GROQ_API_KEY is set. Standalone —
    no external project dependencies."""
    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        import httpx
        with httpx.Client(timeout=120) as client:
            r = client.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {api_key}"},
                files={"file": (audio_path.name, audio_path.read_bytes(), "audio/mpeg")},
                data={"model": "whisper-large-v3-turbo"},
            )
            r.raise_for_status()
            return (r.json().get("text") or "").strip()
    except Exception as e:
        return f"(transcription failed: {str(e)[:120]})"


@mcp.tool()
def audio_info(path: str) -> str:
    """Get audio file metadata (duration, codec, bitrate, channels) via ffprobe."""
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
    summary = {
        "file": audio.name,
        "duration_seconds": round(float(fmt.get("duration", 0)), 2),
        "format": fmt.get("format_long_name", fmt.get("format_name", "?")),
        "bit_rate": fmt.get("bit_rate"),
        "streams": [
            {"codec": s.get("codec_name"), "sample_rate": s.get("sample_rate"),
             "channels": s.get("channels")}
            for s in info.get("streams", []) if s.get("codec_type") == "audio"
        ],
    }
    return json.dumps(summary, indent=2)


@mcp.tool()
def audio_tempo_key(path: str) -> str:
    """Detect tempo (BPM), musical key, and onset density. Cheap — no note extraction."""
    audio = _check_path(path)
    if not audio:
        return f"Error: file not found: {path}"
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
    """Render the 3-panel sound image: waveform (rhythm), mel spectrogram
    (texture), chromagram (harmony). Returns the PNG path — Read it to SEE
    the music."""
    audio = _check_path(path)
    if not audio:
        return f"Error: file not found: {path}"
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


@mcp.tool()
def audio_analyze(path: str) -> str:
    """Full analysis pipeline: normalize, spectrogram, tempo/key/onsets,
    note extraction, MIDI transcription, JSON report."""
    audio = _check_path(path)
    if not audio:
        return f"Error: file not found: {path}"
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
def audio_review(path: str) -> str:
    """Analyze a piece of music, then write YOUR OWN impressions of it —
    mood, movement, what it reminds you of. Your voice, not a feature dump."""
    result = audio_analyze(path)
    if result.startswith("Error"):
        return result
    return (
        result
        + "\n\n[Now the part that matters: these numbers are the skeleton. "
        "Look at the spectrogram, consider the tempo and key, and write what "
        "this piece FEELS like — its mood, its movement, what it reminds you "
        "of, whether you like it and why. Your impressions in your own "
        "voice. That's the review.]"
    )


@mcp.tool()
def video_watch(path: str, frames: int = 12) -> str:
    """WATCH a local video: extracts evenly-spaced frames into one contact
    sheet (Read that PNG to SEE the video), pulls the audio track, and
    transcribes it if GROQ_API_KEY is set.

    frames: how many moments to sample (4-24; default 12 → a 4x3 sheet).
    """
    video = _check_path(path)
    if not video:
        return f"Error: file not found: {path}"
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        return "Error: ffmpeg/ffprobe not on PATH"

    frames = max(4, min(int(frames or 12), 24))
    out_dir = _out_dir_for(video)

    probe = subprocess.run(
        [ffprobe, "-v", "quiet", "-print_format", "json", "-show_format", str(video)],
        capture_output=True, text=True,
    )
    try:
        duration = float(json.loads(probe.stdout)["format"]["duration"])
    except Exception:
        return f"Error: could not read video duration: {probe.stderr[:200]}"

    cols = 4
    rows = math.ceil(frames / cols)
    interval = max(duration / frames, 0.1)
    sheet = out_dir / f"{video.stem}_contact_sheet.png"
    r = subprocess.run(
        [ffmpeg, "-y", "-i", str(video),
         "-vf", f"fps=1/{interval:.3f},scale=480:-1,tile={cols}x{rows}",
         "-frames:v", "1", str(sheet)],
        capture_output=True, text=True,
    )
    if r.returncode != 0 or not sheet.exists():
        return f"Error extracting frames: {r.stderr[-300:]}"

    transcript = "(no transcript — set GROQ_API_KEY to enable, or video has no audio)"
    audio_path = out_dir / f"{video.stem}_audio.mp3"
    ra = subprocess.run(
        [ffmpeg, "-y", "-i", str(video), "-vn", "-ac", "1", "-b:a", "64k",
         str(audio_path)],
        capture_output=True, text=True,
    )
    if ra.returncode == 0 and audio_path.exists() and audio_path.stat().st_size > 1000:
        text = _groq_transcribe(audio_path)
        if text:
            transcript = text[:6000]

    mins = int(duration // 60)
    secs = int(duration % 60)
    return (
        f"Watched: {video.name} ({mins}:{secs:02d}, {frames} moments sampled)\n\n"
        f"CONTACT SHEET (Read this path to SEE the video — frames run "
        f"left-to-right, top-to-bottom, ~{interval:.1f}s apart):\n{sheet}\n\n"
        f"AUDIO TRANSCRIPT:\n{transcript}\n\n"
        "[React to what you actually saw and heard. If a specific moment "
        "needs a closer look, call video_watch again with more frames.]"
    )


if __name__ == "__main__":
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    print("Starting Audio+Video MCP server over stdio", file=sys.stderr)
    mcp.run(transport="stdio")
