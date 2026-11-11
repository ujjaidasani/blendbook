// Live listening: measures loudness and bass / mid / high balance once a second, from a mic, a line-in, a loopback
// input, or this computer's own sound (screen-share audio). Only those numbers are kept, never the audio: rekordbox
// won't record streamed tracks, so Blendbook doesn't either.
//
// The measuring runs in an AudioWorklet (meter-worklet.js) on the audio thread, so it keeps going while rekordbox
// covers this window (timers in a background tab get throttled; worklet messages don't).

const toDb = (rms) => Math.max(-60, 20 * Math.log10(rms + 1e-9));
const band01 = (rms) => Math.min(1, Math.max(0, (toDb(rms) + 70) / 60)); // -70 dB → 0, -10 dB → 1
const SEND_EVERY = 10; // seconds
const RAW_AUDIO = { echoCancellation: false, noiseSuppression: false, autoGainControl: false };
export const SYSTEM_SOURCE = "system";

// Audio inputs the browser can see. Chrome hides them (and their names) until mic access has been allowed once.
export async function audioInputs() {
  const devices = await navigator.mediaDevices?.enumerateDevices?.() || [];
  return devices.filter((d) => d.kind === "audioinput" && d.deviceId && d.deviceId !== "default" && d.deviceId !== "communications")
    .map((d, i) => ({ id: d.deviceId, label: d.label || `Input ${i + 1}` }));
}

// Ask for mic access once so every input (BlackHole, line-in) shows up by name.
export async function unlockInputs() {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  stream.getTracks().forEach((t) => t.stop());
  return audioInputs();
}

async function openSource(source) {
  if (source !== SYSTEM_SOURCE) {
    return navigator.mediaDevices.getUserMedia({ audio: source ? { ...RAW_AUDIO, deviceId: { exact: source } } : RAW_AUDIO });
  }
  if (!navigator.mediaDevices?.getDisplayMedia) throw new Error("This browser can't capture this computer's sound. Use Chrome or Edge.");
  // Chrome only shares sound alongside a screen, so ask for one and drop the picture straight away
  const stream = await navigator.mediaDevices.getDisplayMedia({
    video: true, audio: RAW_AUDIO, systemAudio: "include", selfBrowserSurface: "exclude", surfaceSwitching: "exclude",
  });
  stream.getVideoTracks().forEach((t) => { t.stop(); stream.removeTrack(t); });
  if (!stream.getAudioTracks().length) {
    throw new Error("No sound came through. Share Entire Screen and switch on “Also share system audio” in Chrome's picker.");
  }
  return stream;
}

export async function startListening({ source = "", onLevel, onError, onEnded } = {}) {
  if (!navigator.mediaDevices?.getUserMedia || !window.AudioWorkletNode) {
    throw new Error("Live listening needs Chrome or Edge.");
  }
  const stream = await openSource(source);
  const ctx = new AudioContext();
  await ctx.audioWorklet.addModule(new URL("./meter-worklet.js", import.meta.url));

  const src = ctx.createMediaStreamSource(stream);
  const low = ctx.createBiquadFilter(); low.type = "lowpass"; low.frequency.value = 200;
  const midHp = ctx.createBiquadFilter(); midHp.type = "highpass"; midHp.frequency.value = 200;
  const midLp = ctx.createBiquadFilter(); midLp.type = "lowpass"; midLp.frequency.value = 2000;
  const high = ctx.createBiquadFilter(); high.type = "highpass"; high.frequency.value = 2000;
  const merger = ctx.createChannelMerger(4);
  src.connect(merger, 0, 0);
  src.connect(low).connect(merger, 0, 1);
  src.connect(midHp).connect(midLp).connect(merger, 0, 2);
  src.connect(high).connect(merger, 0, 3);
  const meter = new AudioWorkletNode(ctx, "db-meter", { numberOfInputs: 1, channelCount: 4, channelCountMode: "explicit" });
  merger.connect(meter);
  const sink = ctx.createGain(); sink.gain.value = 0; // keep the graph pulling without making sound
  meter.connect(sink).connect(ctx.destination);

  const session = (crypto.randomUUID?.() || String(Date.now()));
  const startMs = Date.now();
  let buf = { loudness_db: [], low: [], mid: [], high: [] };
  let seconds = 0;
  let silentFor = 0; // seconds of exact digital silence: nothing at all is reaching the capture

  const send = async (final = false) => {
    if (!buf.loudness_db.length) return;
    const body = JSON.stringify({ client_session: session, start_ms: startMs, ...buf });
    buf = { loudness_db: [], low: [], mid: [], high: [] };
    try {
      if (final && navigator.sendBeacon) navigator.sendBeacon("/api/listen", new Blob([body], { type: "application/json" }));
      else await fetch("/api/listen", { method: "POST", headers: { "Content-Type": "application/json" }, body });
    } catch (e) {
      onError?.("Couldn't save the last few seconds of listening.");
    }
  };

  meter.port.onmessage = ({ data: [full, l, m, hi] }) => {
    const db = toDb(full);
    buf.loudness_db.push(Math.round(db * 10) / 10);
    buf.low.push(Math.round(band01(l) * 100) / 100);
    buf.mid.push(Math.round(band01(m) * 100) / 100);
    buf.high.push(Math.round(band01(hi) * 100) / 100);
    seconds += 1;
    silentFor = full === 0 ? silentFor + 1 : 0;
    onLevel?.({ db, seconds, silentFor });
    if (buf.loudness_db.length >= SEND_EVERY) send();
  };

  // "Stop sharing" in Chrome's bar, or the mic unplugged
  stream.getAudioTracks()[0]?.addEventListener("ended", () => onEnded?.());

  const onHide = () => send(true);
  addEventListener("pagehide", onHide);

  return {
    async stop() {
      removeEventListener("pagehide", onHide);
      await send();
      stream.getTracks().forEach((t) => t.stop());
      await ctx.close();
    },
  };
}
