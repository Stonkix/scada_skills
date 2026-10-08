let ctx: AudioContext | null = null

/** Two-tone alarm for critical alerts. Browsers allow audio only after a user gesture; failing silently is fine. */
export function alarm() {
  try {
    ctx ??= new AudioContext()
    const now = ctx.currentTime
    for (const [i, freq] of [880, 660, 880].entries()) {
      const osc = ctx.createOscillator()
      const gain = ctx.createGain()
      osc.type = 'square'
      osc.frequency.value = freq
      gain.gain.setValueAtTime(0.0001, now + i * 0.18)
      gain.gain.exponentialRampToValueAtTime(0.08, now + i * 0.18 + 0.02)
      gain.gain.exponentialRampToValueAtTime(0.0001, now + i * 0.18 + 0.16)
      osc.connect(gain).connect(ctx.destination)
      osc.start(now + i * 0.18)
      osc.stop(now + i * 0.18 + 0.17)
    }
  } catch {
    /* no audio available */
  }
}
