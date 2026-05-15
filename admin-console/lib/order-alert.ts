/**
 * Audio chime + per-device preferences for the orders inbox.
 *
 * Synthesised via Web Audio API instead of an MP3 asset so we don't need
 * to ship and license a sound file. Two-tone bell (A5 → E6) with quick
 * exponential decay; total duration ~450ms. AudioContext requires a user
 * gesture before audio can play (autoplay policy), so the consumer must
 * call unlockAndPlayTest() from a click handler at least once per
 * device. After that, playChime() works for the rest of the session.
 */

const STORAGE_KEY = "orders:alerts-enabled"

let audioCtx: AudioContext | null = null

function getAudioContext(): AudioContext | null {
  if (typeof window === "undefined") return null
  if (audioCtx) return audioCtx
  const Ctor =
    window.AudioContext ||
    (window as unknown as { webkitAudioContext?: typeof AudioContext })
      .webkitAudioContext
  if (!Ctor) return null
  audioCtx = new Ctor()
  return audioCtx
}

function playTone(
  ctx: AudioContext,
  freq: number,
  startOffset: number,
  duration: number,
  peak: number
) {
  const now = ctx.currentTime + startOffset
  const osc = ctx.createOscillator()
  const gain = ctx.createGain()
  osc.type = "sine"
  osc.frequency.value = freq
  gain.gain.setValueAtTime(0, now)
  gain.gain.linearRampToValueAtTime(peak, now + 0.01)
  gain.gain.exponentialRampToValueAtTime(0.0001, now + duration)
  osc.connect(gain).connect(ctx.destination)
  osc.start(now)
  osc.stop(now + duration)
}

export function getAlertsEnabled(): boolean {
  if (typeof window === "undefined") return false
  return localStorage.getItem(STORAGE_KEY) === "true"
}

export function setAlertsEnabled(enabled: boolean): void {
  if (typeof window === "undefined") return
  localStorage.setItem(STORAGE_KEY, String(enabled))
}

/**
 * Play the chime if audio is unlocked. No-op when the AudioContext is
 * suspended (caller hasn't yet completed unlockAndPlayTest from a user
 * gesture). Never throws.
 */
export function playChime(): void {
  const ctx = getAudioContext()
  if (!ctx || ctx.state !== "running") return
  try {
    playTone(ctx, 880, 0, 0.4, 0.3) // A5
    playTone(ctx, 1318.5, 0.05, 0.45, 0.2) // E6
  } catch (err) {
    console.error("[order-alert] playChime failed", err)
  }
}

/**
 * Resume/init the AudioContext from a user gesture and play one test
 * chime to confirm. Returns true if audio is now playable for the
 * remainder of this page session.
 */
export async function unlockAndPlayTest(): Promise<boolean> {
  const ctx = getAudioContext()
  if (!ctx) return false
  try {
    if (ctx.state === "suspended") {
      await ctx.resume()
    }
    if (ctx.state !== "running") return false
    playTone(ctx, 880, 0, 0.4, 0.3)
    playTone(ctx, 1318.5, 0.05, 0.45, 0.2)
    return true
  } catch (err) {
    console.error("[order-alert] unlock failed", err)
    return false
  }
}
