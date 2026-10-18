// Native Discord voice messages need an Ogg/Opus file plus its duration and a waveform.
// Both are read straight from the Ogg container, without decoding any audio:
// - duration comes from the last page's granule position (48 kHz samples) minus the pre-skip;
// - the waveform comes from Opus packet sizes. Opus is variable-bitrate, so quiet stretches
//   produce tiny packets and speech produces large ones, which traces the loudness shape.

export interface VoiceInfo {
  durationSecs: number;
  waveform: string; // base64, one byte (0-255) per point, at most 256 points
}

const OPUS_RATE = 48000;
const MAX_POINTS = 256;

function isOggPage(bytes: Uint8Array, at: number): boolean {
  return bytes[at] === 0x4f && bytes[at + 1] === 0x67 && bytes[at + 2] === 0x67 && bytes[at + 3] === 0x53; // "OggS"
}

// Returns null if this isn't an Ogg/Opus file we can describe
export function describeOggOpus(bytes: Uint8Array): VoiceInfo | null {
  if (bytes.length < 28 || !isOggPage(bytes, 0)) return null;
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);

  const packetSizes: number[] = [];
  let current = 0;
  let lastGranule = 0n;
  let preSkip = 0;
  let packetIndex = 0;
  let offset = 0;

  while (offset + 27 <= bytes.length && isOggPage(bytes, offset)) {
    const granule = view.getBigInt64(offset + 6, true);
    const segments = bytes[offset + 26];
    const tableStart = offset + 27;
    let dataOffset = tableStart + segments;
    if (dataOffset > bytes.length) return null;
    if (granule > 0n) lastGranule = granule;

    for (let i = 0; i < segments; i++) {
      const lacing = bytes[tableStart + i];
      current += lacing;
      if (lacing < 255) {
        // A packet ends here. Packet 0 is OpusHead, packet 1 is OpusTags, the rest is audio
        if (packetIndex === 0) {
          const head = bytes.subarray(dataOffset + lacing - current, dataOffset + lacing);
          if (head.length < 12 || new TextDecoder().decode(head.subarray(0, 8)) !== 'OpusHead') return null;
          preSkip = head[10] | (head[11] << 8);
        } else if (packetIndex > 1) {
          packetSizes.push(current);
        }
        packetIndex++;
        current = 0;
      }
      dataOffset += lacing;
    }
    offset = dataOffset;
  }

  const samples = Number(lastGranule) - preSkip;
  if (packetSizes.length === 0 || samples <= 0) return null;
  const durationSecs = samples / OPUS_RATE;

  // Discord's clients sample at most every 100 ms, up to 256 points
  const points = Math.max(1, Math.min(MAX_POINTS, Math.ceil(durationSecs * 10), packetSizes.length));
  const buckets = new Array<number>(points).fill(0);
  const counts = new Array<number>(points).fill(0);
  packetSizes.forEach((size, i) => {
    const b = Math.min(points - 1, Math.floor((i / packetSizes.length) * points));
    buckets[b] += size;
    counts[b]++;
  });
  const means = buckets.map((sum, i) => (counts[i] ? sum / counts[i] : 0));
  const floor = Math.min(...means);
  const range = Math.max(...means) - floor || 1;
  const levels = means.map(m => Math.round(Math.sqrt((m - floor) / range) * 255));

  let binary = '';
  for (const level of levels) binary += String.fromCharCode(level);
  return { durationSecs: Math.round(durationSecs * 100) / 100, waveform: btoa(binary) };
}
