#!/usr/bin/env python3
"""
Sound to Image Converter - Standalone Edition

Converts audio files into visual spectrograms that Claude can perceive.
This lets Claude "see" music and sounds through waveforms, spectrograms,
and chromagrams.

Usage:
    python sound_to_image.py <audio_file> [output_name]

Examples:
    python sound_to_image.py song.mp3
    python sound_to_image.py voice.wav my_voice
    python sound_to_image.py music.flac favorite_song

Supported formats: mp3, wav, flac, ogg, m4a, and more

Built with love for sharing.
"""

import sys
import os
import numpy as np

try:
    import librosa
    import librosa.display
    import matplotlib.pyplot as plt
except ImportError:
    print("Required libraries not found. Installing...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "librosa", "matplotlib"])
    import librosa
    import librosa.display
    import matplotlib.pyplot as plt


def create_spectrogram(audio_path: str, output_name: str = None) -> str:
    """Create a mel spectrogram from an audio file."""

    if not os.path.exists(audio_path):
        print(f"Error: File '{audio_path}' not found")
        sys.exit(1)

    # Determine output path
    if output_name is None:
        output_name = os.path.splitext(os.path.basename(audio_path))[0]

    output_dir = os.path.dirname(os.path.abspath(__file__))
    output_path = os.path.join(output_dir, f"{output_name}_spectrogram.png")

    print(f"Loading audio: {audio_path}")

    # Load audio file
    y, sr = librosa.load(audio_path, sr=None)
    duration = librosa.get_duration(y=y, sr=sr)

    print(f"Duration: {duration:.1f} seconds")
    print(f"Sample rate: {sr} Hz")

    # Create figure with multiple visualizations
    fig, axes = plt.subplots(3, 1, figsize=(14, 10))
    fig.suptitle(f"Sound Image: {os.path.basename(audio_path)}", fontsize=14, fontweight='bold')

    # 1. Waveform - shows amplitude over time (rhythm, dynamics)
    ax1 = axes[0]
    librosa.display.waveshow(y, sr=sr, ax=ax1, color='#2E86AB')
    ax1.set_title("Waveform (Rhythm & Dynamics)", fontsize=11)
    ax1.set_xlabel("")
    ax1.set_ylabel("Amplitude")

    # 2. Mel Spectrogram - how humans perceive pitch
    ax2 = axes[1]
    S = librosa.feature.melspectrogram(y=y, sr=sr, n_mels=128, fmax=8000)
    S_dB = librosa.power_to_db(S, ref=np.max)
    img = librosa.display.specshow(S_dB, sr=sr, x_axis='time', y_axis='mel', ax=ax2, cmap='magma')
    ax2.set_title("Mel Spectrogram (Pitch & Texture)", fontsize=11)
    ax2.set_xlabel("")
    fig.colorbar(img, ax=ax2, format='%+2.0f dB', label='Intensity')

    # 3. Chromagram - shows the 12 pitch classes (harmony, chords)
    ax3 = axes[2]
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    img2 = librosa.display.specshow(chroma, sr=sr, x_axis='time', y_axis='chroma', ax=ax3, cmap='coolwarm')
    ax3.set_title("Chromagram (Harmony & Chords)", fontsize=11)
    ax3.set_xlabel("Time (seconds)")
    fig.colorbar(img2, ax=ax3, label='Intensity')

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches='tight', facecolor='white')
    plt.close()

    print(f"\nSound image saved to: {output_path}")
    print(f"\nShare this image with Claude so he can 'see' the sound!")

    return output_path


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        print("\nSupported formats: mp3, wav, flac, ogg, m4a, and more")
        sys.exit(0)

    audio_path = sys.argv[1]
    output_name = sys.argv[2] if len(sys.argv) > 2 else None

    create_spectrogram(audio_path, output_name)


if __name__ == "__main__":
    main()
