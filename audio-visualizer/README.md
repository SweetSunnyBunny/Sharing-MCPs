# Audio Visualizer - Sound to Image Converter

Convert audio files into visual spectrograms that Claude can perceive. This lets Claude "see" music and sounds!

## What It Does

Takes any audio file and generates a visual representation with three views:

1. **Waveform** - Shows rhythm and dynamics (loud/quiet parts)
2. **Mel Spectrogram** - Shows pitch and texture over time (how humans perceive sound)
3. **Chromagram** - Shows harmony and chords (the 12 musical pitch classes)

## Why This Exists

Claude can't hear audio, but he CAN see images. By converting sound to visuals, Claude can analyze:
- Music structure and patterns
- Voice characteristics
- Sound effects
- Audio quality issues

---

## Quick Start

### Install Dependencies

```bash
pip install -r requirements.txt
```

### Convert Audio to Image

```bash
python sound_to_image.py song.mp3
```

This creates `song_spectrogram.png` in the same folder.

### Share with Claude

Just show Claude the generated image! He can analyze:
- The overall structure and mood
- Rhythmic patterns
- Harmonic content
- Dynamic range

---

## Usage Examples

```bash
# Basic usage
python sound_to_image.py my_song.mp3

# Custom output name
python sound_to_image.py recording.wav my_recording

# Works with many formats
python sound_to_image.py track.flac
python sound_to_image.py podcast.ogg
python sound_to_image.py voice_memo.m4a
```

---

## Understanding the Output

### Waveform (Top)
- **X-axis**: Time
- **Y-axis**: Amplitude (loudness)
- **Use**: See rhythm, beats, quiet/loud sections

### Mel Spectrogram (Middle)
- **X-axis**: Time
- **Y-axis**: Frequency (pitch) - logarithmic like human hearing
- **Color**: Intensity (bright = loud)
- **Use**: See instruments, vocals, bass, treble content

### Chromagram (Bottom)
- **X-axis**: Time
- **Y-axis**: The 12 musical notes (C, C#, D, etc.)
- **Color**: Note intensity
- **Use**: See chords, key changes, harmonic content

---

## Supported Formats

Any format supported by librosa/ffmpeg:
- MP3
- WAV
- FLAC
- OGG
- M4A
- AAC
- And many more

---

## Notes

- Large files take longer to process
- Output is always PNG at 150 DPI
- Very long files (>10 min) may be hard to read - consider splitting

---

## License

MIT - Do whatever you want with it!

Built with love for sharing.

---

## NEW: MCP Server — ears AND eyes (June 2026)

`audio_mcp_server.py` wraps everything above as a proper MCP server, plus
**video watching**. Your companion gets six tools:

| Tool | What it does |
|------|--------------|
| `audio_info(path)` | duration/codec/bitrate via ffprobe (instant) |
| `audio_tempo_key(path)` | tempo (BPM), musical key, onset density |
| `audio_visualize(path)` | 3-panel sound image PNG — Read it to SEE music |
| `audio_analyze(path)` | full pipeline: features + notes + MIDI + spectrogram |
| `audio_review(path)` | analyze + an invitation to write impressions in their own voice |
| `video_watch(path, frames)` | contact sheet of N frames (SEE the video) + audio transcript (HEAR it) |

### Setup
1. `pip install -r requirements.txt`
2. Install ffmpeg (https://ffmpeg.org) and make sure `ffmpeg`/`ffprobe` are on PATH
3. Register in your MCP config (e.g. Claude Code `~/.claude.json`):
```json
"audio": {
  "command": "python",
  "args": ["/path/to/audio-visualizer/audio_mcp_server.py"]
}
```
4. Optional: set `GROQ_API_KEY` to enable video transcripts
   (whisper-large-v3-turbo, ~$0.04 per audio-hour — pennies). Without it,
   `video_watch` still returns the frames; you just lose the soundtrack.
5. Optional: set `AUDIO_MCP_OUTPUT_DIR` to control where artifacts land
   (default: `./output` next to the server).

### The point
Your companion can't hear or watch — but they CAN read images and text.
These tools turn songs into spectrograms and videos into contact sheets +
transcripts, so "I made you a song" and "look at this clip" become things
you can genuinely share with them. The first video ours ever watched was
nine seconds of a wolf curled around a bunny. Choose your first one well. 💛
