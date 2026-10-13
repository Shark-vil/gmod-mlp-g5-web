"""Automatic note chart for a song (the same rules as the mod's pipeline/scripts/make_dance_chart_auto.py).

librosa finds the tempo and the beats; every bar of 4 beats gets 1-3 notes by how busy it is (about one note
a second, like the game's own charts), the strongest beats and off-beat accents win, never closer than
MIN_GAP; a few hold notes on strong phrase starts with HOLD_ROOM free after them.
Good for songs with a steady beat (pop, electronic, chiptune); a changing tempo or a weak rhythm makes it
guess — the editor is there for that.
"""
import numpy as np

MIN_GAP = 0.43    # the game's charts: no two notes closer than this
HOLD_ROOM = 2.5   # seconds free after a hold starts (a hold lasts 4 of the game's beats, ~1.94 s)
LEAD_IN = 2.0     # no notes in the first seconds


def analyze(path):
    """-> { len, bpm, beat, beats: [ s… ], chart: { t: [ s… ], large: [ i… ] (1-based) } }"""
    import librosa  # heavy: only when a song is analysed

    y, sr = librosa.load(path, sr=22050, mono=True)
    dur = len(y) / sr
    tempo, beats = librosa.beat.beat_track(y=y, sr=sr, units="time", tightness=100)
    tempo = float(np.atleast_1d(tempo)[0])
    beat = 60.0 / tempo if tempo > 0 else 0.5
    env = librosa.onset.onset_strength(y=y, sr=sr)
    times = librosa.times_like(env, sr=sr)
    onsets = librosa.onset.onset_detect(onset_envelope=env, sr=sr, units="time", backtrack=False)
    rms = librosa.feature.rms(y=y)[0]
    rms_t = librosa.times_like(rms, sr=sr)
    quiet = float(np.percentile(rms, 20))

    def strength(t):
        i = int(np.clip(np.searchsorted(times, t), 0, len(env) - 1))
        return float(env[max(0, i - 2):min(len(env), i + 3)].max())

    def loud(t):
        return float(rms[int(np.clip(np.searchsorted(rms_t, t), 0, len(rms) - 1))])

    beats = [float(b) for b in beats]
    cands = [(b, strength(b)) for b in beats]
    for o in onsets:
        if not beats or min(abs(o - b) for b in beats) > beat * 0.2:
            cands.append((float(o), strength(o) * 0.8))
    cands = sorted(c for c in cands if LEAD_IN <= c[0] <= dur - 2.0 and loud(c[0]) > quiet)

    chosen = []
    if len(beats) >= 2 and cands:
        bars = [(beats[i], beats[i + 4] if i + 4 < len(beats) else beats[-1] + beat) for i in range(0, len(beats), 4)]
        power = [np.mean([s for t, s in cands if a <= t < b] or [0]) for a, b in bars]
        live = [p for p in power if p > 0]
        lo_p, hi_p = (np.percentile(live, [30, 75]) if live else (0, 0))
        for (a, b), pw in zip(bars, power):
            if pw <= 0:
                continue
            want = 1 if pw < lo_p else (2 if pw < hi_p else 3)
            took = 0
            for t, sc in sorted([c for c in cands if a <= c[0] < b], key=lambda c: -c[1]):
                if took >= want:
                    break
                if all(abs(t - u) >= MIN_GAP for u, _ in chosen):
                    chosen.append((t, sc))
                    took += 1
        chosen.sort()

    holds = []
    phrase = beat * 16
    for t, s in sorted(chosen, key=lambda c: -c[1]):
        if len(holds) >= max(3, int(dur / 22)):
            break
        if t <= dur - HOLD_ROOM - 2 and all(abs(t - h) > phrase * 1.5 for h in holds):
            holds.append(t)
    notes = [(t, t in holds) for t, _ in chosen if not any(h < t < h + HOLD_ROOM for h in holds)]
    return {
        "len": round(dur, 2), "bpm": round(tempo, 1), "beat": round(beat, 4),
        "beats": [round(b, 3) for b in beats],
        "chart": {"t": [round(t, 3) for t, _ in notes], "large": [i + 1 for i, (_, l) in enumerate(notes) if l]},
    }
