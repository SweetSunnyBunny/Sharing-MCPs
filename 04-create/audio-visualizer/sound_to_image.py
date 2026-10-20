#!/usr/bin/env python3
"""
Audio Analyzer & Visualizer
Converts audio files into visual spectrograms, extracts notes, transcribes to MIDI,
and provides detailed audio metadata.

Usage:
    python sound_to_image.py visualize <audio_file> [output_name]
    python sound_to_image.py analyze <audio_file> [output_dir]
    python sound_to_image.py spectrogram <audio_file> [output_name]
    python sound_to_image.py info <audio_file>
    python sound_to_image.py midi-json <midi_file>
    python sound_to_image.py doctor

Commands:
    visualize    Full 3-panel visualization (waveform, mel spectrogram, chromagram)
    analyze      Full pipeline: normalize, spectrogram, MIDI transcription, JSON notes
    spectrogram  Quick spectrogram via ffmpeg (fast, no Python audio libs needed)
    info         Audio metadata via ffprobe (codecs, duration, tags, streams)
    midi-json    Parse a MIDI file into structured JSON
    doctor       Check environment: Python, ffmpeg, ffprobe, libraries
"""

import sys
import os
import json
import struct
import subprocess
import shutil
import argparse

import numpy as np

try:
    import librosa
    import librosa.display
    import matplotlib.pyplot as plt
    HAS_LIBROSA = True
except ImportError:
    HAS_LIBROSA = False

try:
    from scipy.io import wavfile as scipy_wavfile
    from scipy.signal import correlate, find_peaks
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
FRAME_SIZE = 2048
HOP_SIZE = 512
RMS_THRESHOLD = 0.015
MIN_FREQ = 55.0    # A1
MAX_FREQ = 1760.0  # A6
MIN_NOTE_DURATION = 0.045  # seconds
MERGE_GAP = 0.030  # seconds
MIDI_NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F',
                   'F#', 'G', 'G#', 'A', 'A#', 'B']


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def freq_to_midi(freq):
    """Convert frequency in Hz to MIDI note number."""
    if freq <= 0:
        return 0
    return int(round(69 + 12 * np.log2(freq / 440.0)))


def midi_to_note_name(midi_num):
    """Convert MIDI note number to human-readable name like C4."""
    octave = (midi_num // 12) - 1
    note = MIDI_NOTE_NAMES[midi_num % 12]
    return f"{note}{octave}"


def ensure_install(*packages):
    """Auto-install missing Python packages."""
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", *packages],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )


# ---------------------------------------------------------------------------
# Music feature analysis (tempo, key, onsets) — requires librosa
# ---------------------------------------------------------------------------
# Krumhansl-Schmuckler key profiles (1990) — perceptual weights for each
# scale degree in major and minor keys. Used by correlating against the
# average chroma of a track and picking the best-fitting rotation.
KRUMHANSL_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09,
                            2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
KRUMHANSL_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53,
                            2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


def estimate_key(chroma_mean):
    """
    Estimate musical key from a 12-bin chroma vector via
    Krumhansl-Schmuckler correlation against major/minor profiles.
    Returns dict with key name, mode, confidence, and top alternatives.
    """
    scores = []
    for shift in range(12):
        major_profile = np.roll(KRUMHANSL_MAJOR, shift)
        minor_profile = np.roll(KRUMHANSL_MINOR, shift)
        major_score = float(np.corrcoef(chroma_mean, major_profile)[0, 1])
        minor_score = float(np.corrcoef(chroma_mean, minor_profile)[0, 1])
        scores.append((MIDI_NOTE_NAMES[shift], 'major', major_score))
        scores.append((MIDI_NOTE_NAMES[shift], 'minor', minor_score))

    scores.sort(key=lambda s: s[2], reverse=True)
    best_root, best_mode, best_score = scores[0]
    alternatives = [
        {'name': f"{r} {m}", 'confidence': round(s, 4)}
        for r, m, s in scores[:5]
    ]
    return {
        'name': f"{best_root} {best_mode}",
        'root': best_root,
        'mode': best_mode,
        'confidence': round(best_score, 4),
        'alternatives': alternatives,
    }


def compute_music_features(wav_path):
    """
    Use librosa to extract tempo (BPM), beat times, onset times, and key
    from a WAV file. Returns a dict on success, or a dict with an 'error'
    key if librosa is missing or fails at runtime (e.g. numpy/numba
    version mismatch). Never raises — analyze pipeline must keep going.
    """
    if not HAS_LIBROSA:
        return {'error': 'librosa not installed'}

    try:
        y, sr = librosa.load(wav_path, sr=None)

        # Tempo + beat tracking
        tempo, beats = librosa.beat.beat_track(y=y, sr=sr)
        tempo_bpm = float(np.atleast_1d(tempo)[0])
        beat_times = librosa.frames_to_time(beats, sr=sr)

        # Onset detection (note attacks / transients)
        onset_frames = librosa.onset.onset_detect(y=y, sr=sr)
        onset_times = librosa.frames_to_time(onset_frames, sr=sr)

        # Key estimation via average chroma
        chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
        chroma_mean = np.mean(chroma, axis=1)
        key_info = estimate_key(chroma_mean)

        return {
            'tempo_bpm': round(tempo_bpm, 2),
            'beat_count': int(len(beat_times)),
            'beat_times': [round(float(t), 4) for t in beat_times],
            'onset_count': int(len(onset_times)),
            'onset_times': [round(float(t), 4) for t in onset_times],
            'key': key_info,
        }
    except Exception as exc:
        return {'error': f'{type(exc).__name__}: {exc}'}


# ---------------------------------------------------------------------------
# Pitch detection via autocorrelation
# ---------------------------------------------------------------------------
def estimate_pitch(frame, sr):
    """
    Estimate fundamental frequency of a single audio frame using autocorrelation.
    Returns (frequency_hz, confidence) or (0, 0) if no pitch detected.
    """
    # RMS gate - skip quiet frames
    rms = np.sqrt(np.mean(frame ** 2))
    if rms < RMS_THRESHOLD:
        return 0.0, 0.0

    # Window the frame
    windowed = frame * np.hanning(len(frame))

    # Autocorrelation
    corr = correlate(windowed, windowed, mode='full')
    corr = corr[len(corr) // 2:]  # keep positive lags only

    # Normalize
    if corr[0] == 0:
        return 0.0, 0.0
    corr = corr / corr[0]

    # Search for peaks in the valid frequency range
    min_lag = int(sr / MAX_FREQ)
    max_lag = int(sr / MIN_FREQ)
    max_lag = min(max_lag, len(corr) - 1)

    if min_lag >= max_lag:
        return 0.0, 0.0

    search_region = corr[min_lag:max_lag + 1]
    peaks, properties = find_peaks(search_region, height=0.0)

    if len(peaks) == 0:
        return 0.0, 0.0

    # Pick the highest peak
    best_idx = peaks[np.argmax(properties['peak_heights'])]
    confidence = float(properties['peak_heights'][np.argmax(properties['peak_heights'])])
    lag = best_idx + min_lag
    frequency = sr / lag

    return frequency, confidence


def extract_notes(audio, sr):
    """
    Extract note events from audio using frame-by-frame pitch detection.
    Returns list of dicts with pitch, frequency, start, end, velocity, confidence.
    """
    n_frames = (len(audio) - FRAME_SIZE) // HOP_SIZE + 1
    raw_notes = []

    # Track pitch across frames
    current_midi = None
    current_start = None
    current_confidences = []
    current_rmses = []

    for i in range(n_frames):
        start_sample = i * HOP_SIZE
        frame = audio[start_sample:start_sample + FRAME_SIZE]
        time_sec = start_sample / sr

        freq, conf = estimate_pitch(frame, sr)
        midi_num = freq_to_midi(freq) if freq > 0 else 0
        rms = float(np.sqrt(np.mean(frame ** 2)))

        if midi_num > 0 and midi_num == current_midi:
            # Continue current note
            current_confidences.append(conf)
            current_rmses.append(rms)
        else:
            # End previous note if any
            if current_midi is not None and current_midi > 0:
                end_time = time_sec
                raw_notes.append({
                    'midi': current_midi,
                    'note': midi_to_note_name(current_midi),
                    'frequency': round(440.0 * 2 ** ((current_midi - 69) / 12), 2),
                    'start': round(current_start, 4),
                    'end': round(end_time, 4),
                    'velocity': min(127, int(np.mean(current_rmses) * 127 / 0.5)),
                    'confidence': round(float(np.mean(current_confidences)), 4)
                })

            # Start new note
            if midi_num > 0:
                current_midi = midi_num
                current_start = time_sec
                current_confidences = [conf]
                current_rmses = [rms]
            else:
                current_midi = None
                current_start = None
                current_confidences = []
                current_rmses = []

    # Close final note
    if current_midi is not None and current_midi > 0:
        end_time = len(audio) / sr
        raw_notes.append({
            'midi': current_midi,
            'note': midi_to_note_name(current_midi),
            'frequency': round(440.0 * 2 ** ((current_midi - 69) / 12), 2),
            'start': round(current_start, 4),
            'end': round(end_time, 4),
            'velocity': min(127, int(np.mean(current_rmses) * 127 / 0.5)),
            'confidence': round(float(np.mean(current_confidences)), 4)
        })

    # Post-process: filter short notes and merge close gaps
    notes = [n for n in raw_notes if (n['end'] - n['start']) >= MIN_NOTE_DURATION]
    notes = _merge_close_notes(notes)

    return notes


def _merge_close_notes(notes):
    """Merge consecutive notes of the same pitch separated by tiny gaps."""
    if len(notes) < 2:
        return notes
    merged = [notes[0]]
    for n in notes[1:]:
        prev = merged[-1]
        if n['midi'] == prev['midi'] and (n['start'] - prev['end']) < MERGE_GAP:
            prev['end'] = n['end']
            prev['confidence'] = round((prev['confidence'] + n['confidence']) / 2, 4)
            prev['velocity'] = (prev['velocity'] + n['velocity']) // 2
        else:
            merged.append(n)
    return merged


# ---------------------------------------------------------------------------
# MIDI writer (from scratch, no library needed)
# ---------------------------------------------------------------------------
def _vlq_encode(value):
    """Encode an integer as MIDI variable-length quantity bytes."""
    if value < 0:
        raise ValueError("VLQ value must be non-negative")
    result = []
    result.append(value & 0x7F)
    value >>= 7
    while value:
        result.append((value & 0x7F) | 0x80)
        value >>= 7
    result.reverse()
    return bytes(result)


def write_midi(notes, output_path, tempo_bpm=120):
    """
    Write a list of note dicts to a Standard MIDI File (Format 0).
    Each note needs: midi, start, end, velocity.
    """
    ticks_per_beat = 480
    us_per_beat = int(60_000_000 / tempo_bpm)
    ticks_per_sec = ticks_per_beat * tempo_bpm / 60.0

    # Build track events
    events = []
    for n in notes:
        on_tick = int(n['start'] * ticks_per_sec)
        off_tick = int(n['end'] * ticks_per_sec)
        vel = max(1, min(127, n.get('velocity', 80)))
        events.append((on_tick, 0x90, n['midi'], vel))      # note on
        events.append((off_tick, 0x80, n['midi'], 0))        # note off

    # Sort by tick, note-offs before note-ons at same tick
    events.sort(key=lambda e: (e[0], 0 if e[1] == 0x80 else 1))

    # Build track data
    track_data = bytearray()

    # Tempo meta event at tick 0
    track_data += b'\x00'  # delta=0
    track_data += b'\xFF\x51\x03'
    track_data += struct.pack('>I', us_per_beat)[1:]  # 3 bytes

    # Note events
    prev_tick = 0
    for tick, status, note, vel in events:
        delta = tick - prev_tick
        track_data += _vlq_encode(delta)
        track_data += bytes([status, note, vel])
        prev_tick = tick

    # End of track
    track_data += b'\x00\xFF\x2F\x00'

    # Write file
    with open(output_path, 'wb') as f:
        # MThd
        f.write(b'MThd')
        f.write(struct.pack('>I', 6))       # header length
        f.write(struct.pack('>HHH', 0, 1, ticks_per_beat))  # format 0, 1 track
        # MTrk
        f.write(b'MTrk')
        f.write(struct.pack('>I', len(track_data)))
        f.write(track_data)


# ---------------------------------------------------------------------------
# MIDI parser (from scratch)
# ---------------------------------------------------------------------------
def _vlq_decode(data, offset):
    """Decode a variable-length quantity from MIDI data. Returns (value, new_offset)."""
    value = 0
    while True:
        byte = data[offset]
        value = (value << 7) | (byte & 0x7F)
        offset += 1
        if not (byte & 0x80):
            break
    return value, offset


def parse_midi(midi_path):
    """
    Parse a MIDI file into structured JSON-compatible dict.
    Handles Format 0/1, multi-track, running status, tempo changes.
    """
    with open(midi_path, 'rb') as f:
        data = f.read()

    if data[:4] != b'MThd':
        raise ValueError("Not a valid MIDI file (missing MThd header)")

    header_len = struct.unpack('>I', data[4:8])[0]
    fmt, n_tracks, ticks_per_beat = struct.unpack('>HHH', data[8:14])

    result = {
        'format': fmt,
        'tracks': n_tracks,
        'ticks_per_beat': ticks_per_beat,
        'track_data': []
    }

    offset = 8 + header_len

    for track_idx in range(n_tracks):
        if data[offset:offset + 4] != b'MTrk':
            raise ValueError(f"Expected MTrk at offset {offset}")

        track_len = struct.unpack('>I', data[offset + 4:offset + 8])[0]
        track_start = offset + 8
        track_end = track_start + track_len
        offset = track_end

        # Parse track events
        track_events = []
        pos = track_start
        running_status = None
        abs_tick = 0

        # Tempo map for time conversion
        tempo_map = [(0, 500000)]  # default 120 BPM

        while pos < track_end:
            delta, pos = _vlq_decode(data, pos)
            abs_tick += delta

            byte = data[pos]

            if byte == 0xFF:  # Meta event
                pos += 1
                meta_type = data[pos]
                pos += 1
                length, pos = _vlq_decode(data, pos)
                meta_data = data[pos:pos + length]
                pos += length

                if meta_type == 0x51 and length == 3:  # Tempo
                    us_per_beat = (meta_data[0] << 16) | (meta_data[1] << 8) | meta_data[2]
                    tempo_map.append((abs_tick, us_per_beat))
                    track_events.append({
                        'tick': abs_tick,
                        'type': 'tempo',
                        'bpm': round(60_000_000 / us_per_beat, 2)
                    })
                elif meta_type == 0x03:  # Track name
                    track_events.append({
                        'tick': abs_tick,
                        'type': 'track_name',
                        'name': meta_data.decode('ascii', errors='replace')
                    })
                elif meta_type == 0x2F:  # End of track
                    break

            elif byte == 0xF0 or byte == 0xF7:  # SysEx
                pos += 1
                length, pos = _vlq_decode(data, pos)
                pos += length

            elif byte & 0x80:  # Status byte
                running_status = byte
                pos += 1
                status_high = byte & 0xF0
                channel = byte & 0x0F

                if status_high in (0x80, 0x90, 0xA0, 0xB0, 0xE0):
                    d1 = data[pos]; pos += 1
                    d2 = data[pos]; pos += 1
                    if status_high == 0x90 and d2 > 0:
                        track_events.append({
                            'tick': abs_tick, 'type': 'note_on',
                            'channel': channel, 'note': d1,
                            'note_name': midi_to_note_name(d1), 'velocity': d2
                        })
                    elif status_high == 0x80 or (status_high == 0x90 and d2 == 0):
                        track_events.append({
                            'tick': abs_tick, 'type': 'note_off',
                            'channel': channel, 'note': d1,
                            'note_name': midi_to_note_name(d1)
                        })
                elif status_high in (0xC0, 0xD0):
                    pos += 1  # 1 data byte

            else:  # Running status
                if running_status is not None:
                    status_high = running_status & 0xF0
                    channel = running_status & 0x0F
                    d1 = byte
                    pos += 1

                    if status_high in (0x80, 0x90, 0xA0, 0xB0, 0xE0):
                        d2 = data[pos]; pos += 1
                        if status_high == 0x90 and d2 > 0:
                            track_events.append({
                                'tick': abs_tick, 'type': 'note_on',
                                'channel': channel, 'note': d1,
                                'note_name': midi_to_note_name(d1), 'velocity': d2
                            })
                        elif status_high == 0x80 or (status_high == 0x90 and d2 == 0):
                            track_events.append({
                                'tick': abs_tick, 'type': 'note_off',
                                'channel': channel, 'note': d1,
                                'note_name': midi_to_note_name(d1)
                            })
                    elif status_high in (0xC0, 0xD0):
                        pass  # 1 data byte already consumed

        # Convert ticks to seconds using tempo map
        def tick_to_seconds(tick):
            seconds = 0.0
            prev_tick = 0
            current_tempo = 500000
            for map_tick, tempo in tempo_map:
                if map_tick >= tick:
                    break
                seconds += (map_tick - prev_tick) * current_tempo / (ticks_per_beat * 1_000_000)
                prev_tick = map_tick
                current_tempo = tempo
            seconds += (tick - prev_tick) * current_tempo / (ticks_per_beat * 1_000_000)
            return round(seconds, 4)

        # Build note list by pairing on/off events
        note_ons = {}
        notes = []
        for ev in track_events:
            if ev['type'] == 'note_on':
                key = (ev['channel'], ev['note'])
                note_ons[key] = ev
            elif ev['type'] == 'note_off':
                key = (ev['channel'], ev['note'])
                if key in note_ons:
                    on_ev = note_ons.pop(key)
                    notes.append({
                        'note': ev['note'],
                        'note_name': ev['note_name'],
                        'channel': ev['channel'],
                        'velocity': on_ev['velocity'],
                        'start_tick': on_ev['tick'],
                        'end_tick': ev['tick'],
                        'start_sec': tick_to_seconds(on_ev['tick']),
                        'end_sec': tick_to_seconds(ev['tick'])
                    })

        result['track_data'].append({
            'track': track_idx,
            'events': track_events,
            'notes': notes
        })

    return result


# ---------------------------------------------------------------------------
# Audio normalization (for analyze pipeline)
# ---------------------------------------------------------------------------
def normalize_audio(audio_path, output_path, target_sr=22050):
    """
    Normalize audio to mono WAV at target sample rate using ffmpeg.
    Falls back to librosa if ffmpeg isn't available.
    """
    ffmpeg = shutil.which('ffmpeg')
    if ffmpeg:
        subprocess.run([
            ffmpeg, '-y', '-i', audio_path,
            '-ac', '1', '-ar', str(target_sr),
            '-acodec', 'pcm_s16le', output_path
        ], capture_output=True)
    elif HAS_LIBROSA:
        y, sr = librosa.load(audio_path, sr=target_sr, mono=True)
        import soundfile as sf
        sf.write(output_path, y, target_sr)
    else:
        raise RuntimeError("Neither ffmpeg nor librosa available for audio normalization")


def load_wav_scipy(wav_path):
    """Load a WAV file using scipy, return (audio_float32_array, sample_rate)."""
    sr, data = scipy_wavfile.read(wav_path)
    # Convert to float32 normalized to [-1, 1]
    if data.dtype == np.int16:
        audio = data.astype(np.float32) / 32768.0
    elif data.dtype == np.int32:
        audio = data.astype(np.float32) / 2147483648.0
    elif data.dtype == np.float32:
        audio = data
    else:
        audio = data.astype(np.float32)

    # Convert to mono if stereo
    if len(audio.shape) > 1:
        audio = audio.mean(axis=1)

    # Peak normalize
    peak = np.max(np.abs(audio))
    if peak > 0:
        audio = audio / peak

    return audio, sr


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
def cmd_visualize(args):
    """Full 3-panel visualization (waveform, mel spectrogram, chromagram)."""
    if not HAS_LIBROSA:
        print("Installing required libraries (librosa, matplotlib)...")
        ensure_install('librosa', 'matplotlib')
        print("Libraries installed. Please re-run the command.")
        sys.exit(0)

    audio_path = args.audio_file
    if not os.path.exists(audio_path):
        print(f"Error: File '{audio_path}' not found")
        sys.exit(1)

    output_name = args.output_name
    if output_name is None:
        output_name = os.path.splitext(os.path.basename(audio_path))[0]

    output_dir = os.path.dirname(os.path.abspath(__file__))
    output_path = os.path.join(output_dir, f"{output_name}_spectrogram.png")

    print(f"Loading audio: {audio_path}")
    y, sr = librosa.load(audio_path, sr=None)
    duration = librosa.get_duration(y=y, sr=sr)
    print(f"Duration: {duration:.1f}s | Sample rate: {sr} Hz")

    fig, axes = plt.subplots(3, 1, figsize=(14, 10))
    fig.suptitle(f"Sound Image: {os.path.basename(audio_path)}", fontsize=14, fontweight='bold')

    # Waveform
    librosa.display.waveshow(y, sr=sr, ax=axes[0], color='#2E86AB')
    axes[0].set_title("Waveform (Rhythm & Dynamics)", fontsize=11)
    axes[0].set_xlabel("")
    axes[0].set_ylabel("Amplitude")

    # Mel Spectrogram
    S = librosa.feature.melspectrogram(y=y, sr=sr, n_mels=128, fmax=8000)
    S_dB = librosa.power_to_db(S, ref=np.max)
    img = librosa.display.specshow(S_dB, sr=sr, x_axis='time', y_axis='mel', ax=axes[1], cmap='magma')
    axes[1].set_title("Mel Spectrogram (Pitch & Texture)", fontsize=11)
    axes[1].set_xlabel("")
    fig.colorbar(img, ax=axes[1], format='%+2.0f dB', label='Intensity')

    # Chromagram
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    img2 = librosa.display.specshow(chroma, sr=sr, x_axis='time', y_axis='chroma', ax=axes[2], cmap='coolwarm')
    axes[2].set_title("Chromagram (Harmony & Chords)", fontsize=11)
    axes[2].set_xlabel("Time (seconds)")
    fig.colorbar(img2, ax=axes[2], label='Intensity')

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches='tight', facecolor='white')
    plt.close()

    print(f"Sound image saved to: {output_path}")
    return output_path


def cmd_analyze(args):
    """Full analysis pipeline: normalize, spectrogram, MIDI transcription, JSON notes."""
    audio_path = args.audio_file
    if not os.path.exists(audio_path):
        print(f"Error: File '{audio_path}' not found")
        sys.exit(1)

    if not HAS_SCIPY:
        print("Installing required library (scipy)...")
        ensure_install('scipy')
        print("Installed. Please re-run the command.")
        sys.exit(0)

    base_name = os.path.splitext(os.path.basename(audio_path))[0]
    out_dir = args.output_dir if args.output_dir else f"{base_name}_analysis"
    os.makedirs(out_dir, exist_ok=True)

    print(f"Analyzing: {audio_path}")
    print(f"Output directory: {out_dir}")

    # Step 1: Normalize to WAV
    wav_path = os.path.join(out_dir, f"{base_name}_normalized.wav")
    print("\n[1/5] Normalizing audio...")
    normalize_audio(audio_path, wav_path)
    print(f"  -> {wav_path}")

    # Step 2: Quick spectrogram
    spec_path = os.path.join(out_dir, f"{base_name}_spectrogram.png")
    print("[2/5] Generating spectrogram...")
    ffmpeg = shutil.which('ffmpeg')
    if ffmpeg:
        subprocess.run([
            ffmpeg, '-y', '-i', wav_path,
            '-lavfi', 'showspectrumpic=s=1600x900',
            spec_path
        ], capture_output=True)
        print(f"  -> {spec_path}")
    elif HAS_LIBROSA:
        # Fallback to librosa spectrogram
        y, sr = librosa.load(wav_path, sr=None)
        fig, ax = plt.subplots(1, 1, figsize=(16, 9))
        S = librosa.feature.melspectrogram(y=y, sr=sr, n_mels=128, fmax=8000)
        S_dB = librosa.power_to_db(S, ref=np.max)
        librosa.display.specshow(S_dB, sr=sr, x_axis='time', y_axis='mel', ax=ax, cmap='magma')
        ax.set_title(f"Spectrogram: {base_name}")
        plt.tight_layout()
        plt.savefig(spec_path, dpi=150, bbox_inches='tight')
        plt.close()
        print(f"  -> {spec_path}")
    else:
        print("  -> Skipped (no ffmpeg or librosa available)")

    # Step 3: Tempo, key, and onset detection (librosa)
    print("[3/5] Detecting tempo, key, and onsets...")
    features = compute_music_features(wav_path)
    if 'error' in features:
        print(f"  -> Skipped ({features['error']})")
    else:
        print(f"  -> Tempo: {features['tempo_bpm']} BPM | "
              f"Key: {features['key']['name']} "
              f"(conf {features['key']['confidence']}) | "
              f"Onsets: {features['onset_count']}")

    # Step 4: Pitch detection & note extraction
    print("[4/5] Extracting notes...")
    audio, sr = load_wav_scipy(wav_path)
    notes = extract_notes(audio, sr)
    print(f"  -> Found {len(notes)} notes")

    # Step 5: Write MIDI (using detected tempo if we have it)
    midi_path = os.path.join(out_dir, f"{base_name}_transcription.mid")
    print("[5/5] Writing MIDI transcription...")
    midi_tempo = features.get('tempo_bpm', 120) if 'error' not in features else 120
    write_midi(notes, midi_path, tempo_bpm=midi_tempo)
    print(f"  -> {midi_path}")

    # Write JSON analysis
    analysis = {
        'source_file': os.path.basename(audio_path),
        'sample_rate': sr,
        'duration_seconds': round(len(audio) / sr, 2),
        'music_features': features,
        'notes_detected': len(notes),
        'notes': notes
    }
    json_path = os.path.join(out_dir, f"{base_name}_analysis.json")
    with open(json_path, 'w') as f:
        json.dump(analysis, f, indent=2)
    print(f"  -> {json_path}")

    print(f"\nAnalysis complete! All outputs in: {out_dir}/")
    return out_dir


def cmd_spectrogram(args):
    """Quick spectrogram via ffmpeg's showspectrumpic filter."""
    audio_path = args.audio_file
    if not os.path.exists(audio_path):
        print(f"Error: File '{audio_path}' not found")
        sys.exit(1)

    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        print("Error: ffmpeg not found on PATH. Install ffmpeg or use 'visualize' instead.")
        sys.exit(1)

    output_name = args.output_name
    if output_name is None:
        output_name = os.path.splitext(os.path.basename(audio_path))[0]

    output_path = f"{output_name}_spectrogram.png"

    print(f"Generating quick spectrogram: {audio_path}")
    result = subprocess.run([
        ffmpeg, '-y', '-i', audio_path,
        '-lavfi', 'showspectrumpic=s=1600x900',
        output_path
    ], capture_output=True, text=True)

    if result.returncode != 0:
        print(f"Error: ffmpeg failed\n{result.stderr}")
        sys.exit(1)

    print(f"Spectrogram saved to: {output_path}")
    return output_path


def cmd_info(args):
    """Get audio metadata via ffprobe."""
    audio_path = args.audio_file
    if not os.path.exists(audio_path):
        print(f"Error: File '{audio_path}' not found")
        sys.exit(1)

    ffprobe = shutil.which('ffprobe')
    if not ffprobe:
        # Fallback: use librosa for basic info
        if HAS_LIBROSA:
            y, sr = librosa.load(audio_path, sr=None)
            duration = librosa.get_duration(y=y, sr=sr)
            info = {
                'file': os.path.basename(audio_path),
                'duration_seconds': round(duration, 2),
                'sample_rate': sr,
                'samples': len(y),
                'channels': 1,
                'note': 'Limited info (ffprobe not available, using librosa fallback)'
            }
            print(json.dumps(info, indent=2))
            return info

        print("Error: ffprobe not found on PATH and librosa not available.")
        sys.exit(1)

    result = subprocess.run([
        ffprobe, '-v', 'quiet',
        '-print_format', 'json',
        '-show_format', '-show_streams',
        audio_path
    ], capture_output=True, text=True)

    if result.returncode != 0:
        print(f"Error: ffprobe failed\n{result.stderr}")
        sys.exit(1)

    info = json.loads(result.stdout)
    print(json.dumps(info, indent=2))
    return info


def cmd_midi_json(args):
    """Parse a MIDI file into structured JSON."""
    midi_path = args.midi_file
    if not os.path.exists(midi_path):
        print(f"Error: File '{midi_path}' not found")
        sys.exit(1)

    print(f"Parsing MIDI: {midi_path}")
    result = parse_midi(midi_path)

    # Summary
    total_notes = sum(len(t['notes']) for t in result['track_data'])
    print(f"Format: {result['format']} | Tracks: {result['tracks']} | Notes: {total_notes}")

    # Output JSON
    output_path = os.path.splitext(midi_path)[0] + '_parsed.json'
    with open(output_path, 'w') as f:
        json.dump(result, f, indent=2)
    print(f"Saved to: {output_path}")

    # Also print to stdout
    print(json.dumps(result, indent=2))
    return result


def cmd_doctor(args):
    """Check environment: Python version, tools, libraries."""
    print("Audio Analyzer - Environment Check")
    print("=" * 40)

    # Python
    py_ver = sys.version.split()[0]
    py_ok = sys.version_info >= (3, 8)
    print(f"Python:     {py_ver} {'OK' if py_ok else 'WARN (3.8+ recommended)'}")

    # ffmpeg
    ffmpeg = shutil.which('ffmpeg')
    print(f"ffmpeg:     {'OK (' + ffmpeg + ')' if ffmpeg else 'NOT FOUND'}")

    # ffprobe
    ffprobe = shutil.which('ffprobe')
    print(f"ffprobe:    {'OK (' + ffprobe + ')' if ffprobe else 'NOT FOUND'}")

    # numpy
    try:
        import numpy as np_check
        print(f"numpy:      OK ({np_check.__version__})")
    except ImportError:
        print("numpy:      NOT FOUND")

    # scipy
    if HAS_SCIPY:
        import scipy
        print(f"scipy:      OK ({scipy.__version__})")
    else:
        print("scipy:      NOT FOUND (needed for 'analyze' command)")

    # librosa
    if HAS_LIBROSA:
        print(f"librosa:    OK ({librosa.__version__})")
    else:
        print("librosa:    NOT FOUND (needed for 'visualize' command)")

    # matplotlib
    if HAS_LIBROSA:
        print(f"matplotlib: OK ({plt.matplotlib.__version__})")
    else:
        try:
            import matplotlib
            print(f"matplotlib: OK ({matplotlib.__version__})")
        except ImportError:
            print("matplotlib: NOT FOUND (needed for 'visualize' command)")

    print("=" * 40)

    if not ffmpeg:
        print("\nNote: ffmpeg enables 'spectrogram' and 'info' commands.")
        print("Install: https://ffmpeg.org/download.html")

    print("\nAll core features operational!" if (py_ok and HAS_LIBROSA) else
          "\nSome features may be limited. Install missing dependencies above.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        prog='sound_to_image',
        description='Audio Analyzer & Visualizer - Convert audio to images, notes, and MIDI',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s visualize song.mp3
  %(prog)s visualize song.mp3 my_song
  %(prog)s analyze recording.wav
  %(prog)s analyze recording.wav my_output_dir
  %(prog)s spectrogram podcast.mp3
  %(prog)s info track.flac
  %(prog)s midi-json transcription.mid
  %(prog)s doctor
"""
    )

    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    # visualize
    p_vis = subparsers.add_parser('visualize', help='Full 3-panel visualization')
    p_vis.add_argument('audio_file', help='Path to audio file')
    p_vis.add_argument('output_name', nargs='?', default=None, help='Custom output name')

    # analyze
    p_analyze = subparsers.add_parser('analyze', help='Full pipeline: spectrogram + MIDI + JSON notes')
    p_analyze.add_argument('audio_file', help='Path to audio file')
    p_analyze.add_argument('output_dir', nargs='?', default=None, help='Output directory name')

    # spectrogram
    p_spec = subparsers.add_parser('spectrogram', help='Quick spectrogram via ffmpeg')
    p_spec.add_argument('audio_file', help='Path to audio file')
    p_spec.add_argument('output_name', nargs='?', default=None, help='Custom output name')

    # info
    p_info = subparsers.add_parser('info', help='Audio metadata via ffprobe')
    p_info.add_argument('audio_file', help='Path to audio file')

    # midi-json
    p_midi = subparsers.add_parser('midi-json', help='Parse MIDI file to JSON')
    p_midi.add_argument('midi_file', help='Path to MIDI file')

    # doctor
    subparsers.add_parser('doctor', help='Check environment and dependencies')

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        print("\nSupported audio formats: mp3, wav, flac, ogg, m4a, and more")
        sys.exit(0)

    commands = {
        'visualize': cmd_visualize,
        'analyze': cmd_analyze,
        'spectrogram': cmd_spectrogram,
        'info': cmd_info,
        'midi-json': cmd_midi_json,
        'doctor': cmd_doctor,
    }

    commands[args.command](args)


if __name__ == "__main__":
    main()
