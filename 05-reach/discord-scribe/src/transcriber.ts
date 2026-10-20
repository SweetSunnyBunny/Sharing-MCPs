import { CONFIG } from "./config.js";
import { AudioChunk, pcmToWav } from "./recorder.js";
import { execFile } from "node:child_process";
import { writeFile, unlink, mkdir } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { randomUUID } from "node:crypto";

export interface TranscriptionResult {
  text: string;
  speakers: string[];
  startedAt: Date;
  endedAt: Date;
  durationSeconds: number;
}

/**
 * Transcribe an audio chunk using local faster-whisper.
 *
 * Requires faster-whisper installed:
 *   pip install faster-whisper
 *
 * On first run it downloads the model (~1.5GB for medium, ~500MB for small).
 */
export async function transcribeChunk(chunk: AudioChunk): Promise<TranscriptionResult> {
  const wavBuffer = pcmToWav(chunk.pcmBuffer);
  const durationSeconds = Math.round(
    (chunk.endedAt.getTime() - chunk.startedAt.getTime()) / 1000
  );

  console.log(
    `[Scribe] Transcribing ${durationSeconds}s chunk ` +
    `(${(wavBuffer.length / 1024 / 1024).toFixed(1)}MB) ` +
    `from: ${chunk.speakers.join(", ")}`
  );

  const text = await whisperTranscribe(wavBuffer);

  return {
    text: text.trim(),
    speakers: chunk.speakers,
    startedAt: chunk.startedAt,
    endedAt: chunk.endedAt,
    durationSeconds,
  };
}

/**
 * Run local faster-whisper on a WAV buffer and return the transcribed text.
 */
async function whisperTranscribe(wavBuffer: Buffer): Promise<string> {
  const tempDir = join(tmpdir(), "discord-scribe");
  await mkdir(tempDir, { recursive: true });
  const tempFile = join(tempDir, `chunk-${randomUUID()}.wav`);

  try {
    await writeFile(tempFile, wavBuffer);

    const text = await new Promise<string>((resolve, reject) => {
      const pythonScript = `
import sys
from faster_whisper import WhisperModel
model = WhisperModel("${CONFIG.whisperModel}", device="${CONFIG.whisperDevice}", compute_type="${CONFIG.whisperComputeType}")
segments, info = model.transcribe("${tempFile.replace(/\\/g, "\\\\")}", language="en", beam_size=5, initial_prompt="Discord voice call between moderators discussing server management.")
text = " ".join(segment.text.strip() for segment in segments)
print(text)
`.trim();

      execFile(
        CONFIG.pythonPath,
        ["-c", pythonScript],
        { timeout: 120_000, maxBuffer: 10 * 1024 * 1024 },
        (error, stdout, stderr) => {
          if (error) {
            console.error("[Scribe] Whisper stderr:", stderr);
            reject(new Error(`Whisper transcription failed: ${error.message}`));
            return;
          }
          if (stderr && stderr.includes("Error")) {
            console.warn("[Scribe] Whisper warnings:", stderr);
          }
          resolve(stdout.trim());
        }
      );
    });

    return text;
  } finally {
    await unlink(tempFile).catch(() => {});
  }
}

/**
 * Generate a summary of all transcription chunks using Claude Code CLI.
 * Uses the local claude daemon (OAuth) - no API key needed.
 */
export async function summarizeSession(
  transcriptions: TranscriptionResult[],
  channelName: string,
  sessionDurationMinutes: number
): Promise<string> {
  if (transcriptions.length === 0) {
    return "No audio was captured during this session.";
  }

  const fullTranscript = transcriptions
    .map((t) => {
      const timeLabel = formatTime(t.startedAt);
      return `[${timeLabel}] (${t.speakers.join(", ")}): ${t.text}`;
    })
    .join("\n\n");

  const allSpeakers = [...new Set(transcriptions.flatMap((t) => t.speakers))].join(", ");

  const prompt =
    `You are a meeting notes assistant. Summarize the key points, decisions, and action items ` +
    `from this Discord mod call transcript. Be concise but thorough. Use bullet points. ` +
    `If specific people are mentioned as responsible for tasks, note that.\n\n` +
    `Channel: #${channelName}\n` +
    `Duration: ${sessionDurationMinutes} minutes\n` +
    `Participants: ${allSpeakers}\n\n` +
    `Transcript:\n${fullTranscript}`;

  const summary = await claudeCodeCall(prompt);
  return summary || "Failed to generate summary.";
}

/**
 * Spawn Claude Code CLI for a one-shot prompt and return the text result.
 * Uses OAuth credentials from ~/.claude/.credentials.json - no API key needed.
 */
async function claudeCodeCall(prompt: string): Promise<string> {
  return new Promise<string>((resolve, reject) => {
    const env = { ...process.env };
    // Strip CLAUDECODE to avoid nesting detection
    delete env.CLAUDECODE;

    const args = [
      "-p",
      "--output-format", "text",
      "--model", CONFIG.claudeModel,
      "--max-turns", "1",
      "--permission-mode", "default",
    ];

    const proc = execFile(
      CONFIG.claudeCmd,
      args,
      {
        timeout: 120_000,
        maxBuffer: 10 * 1024 * 1024,
        env,
      },
      (error, stdout, stderr) => {
        if (error) {
          console.error("[Scribe] Claude CLI stderr:", stderr);
          reject(new Error(`Claude CLI failed: ${error.message}`));
          return;
        }
        resolve(stdout.trim());
      }
    );

    // Send prompt via stdin
    if (proc.stdin) {
      proc.stdin.write(prompt);
      proc.stdin.end();
    }
  });
}

function formatTime(date: Date): string {
  return date.toLocaleTimeString("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: true,
  });
}
