#!/usr/bin/env python3
"""
moto_autocut - montaggio automatico di clip in moto per DaVinci Resolve (anche free).

Pipeline:
  1. per ogni clip legge la telemetria IMU (giroscopio + accelerometro) dal file
     (telemetry-parser, lo stesso motore di Gyroflow) e l'audio (vento/motore);
  2. calcola uno "score di interesse" ogni 0.5 s;
  3. trova i momenti migliori, distanziati tra loro;
  4. da' a ogni momento una durata diversa (piu' lunga per i momenti migliori);
  5. se c'e' una musica, fa cadere i tagli sui beat;
  6. scrive i file per Resolve (.fcpxml, .xml, .otio) e un audio gia' mixato (.wav).

Uso rapido:
  python moto_autocut.py "C:\\Moto\\Giro1" --analyze-only        # diagnosi
  python moto_autocut.py "C:\\Moto\\Giro1" -o montaggio          # durata libera
  python moto_autocut.py "C:\\Moto\\Giro1" --music brano.mp3 --length 90 -o montaggio
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

import numpy as np
from scipy.signal import medfilt

DT = 0.5  # risoluzione dello score in secondi
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".insv"}
FCPX_AUDIO_RATES = {32000, 44100, 48000, 88200, 96000, 176400, 192000}


# ----------------------------------------------------------------------------
# Lettura clip
# ----------------------------------------------------------------------------
@dataclass
class Clip:
    path: Path
    duration: float
    fps_frac: Fraction
    width: int
    height: int
    a_ch: int  # canali audio (0 = nessun audio)
    a_rate: int
    feats: dict = field(default_factory=dict)  # nome -> array per bin (DT)
    score: np.ndarray | None = None
    camera: str = "?"
    imu_samples: int = 0

    @property
    def fps(self) -> float:
        return float(self.fps_frac)


def _ffprobe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_type,r_frame_rate,width,height,channels,sample_rate:format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def probe(path: Path) -> Clip:
    j = _ffprobe(path)
    streams = j.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video" and s.get("width")), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if v is None:
        raise ValueError("nessun flusso video")
    fps = Fraction(v["r_frame_rate"])
    if fps <= 0:
        raise ValueError("fps non validi")
    return Clip(path=path, duration=float(j["format"]["duration"]), fps_frac=fps,
                width=int(v["width"]), height=int(v["height"]),
                a_ch=int(a["channels"]) if a else 0,
                a_rate=int(a["sample_rate"]) if a else 0)


def probe_audio(path: Path) -> tuple[float, int, int]:
    """(durata, sample rate, canali) di un file audio."""
    j = _ffprobe(path)
    a = next((s for s in j.get("streams", []) if s.get("codec_type") == "audio"), None)
    if a is None:
        raise ValueError("il file non contiene audio")
    return float(j["format"]["duration"]), int(a["sample_rate"]), int(a["channels"])


_WARNED_NO_TP = False


def read_imu(path: Path):
    """Ritorna (camera, t[s], gyro Nx3, accl Nx3) oppure None se non c'e' IMU."""
    global _WARNED_NO_TP
    try:
        import telemetry_parser
    except ImportError:
        if not _WARNED_NO_TP:
            print("  [imu] telemetry-parser non installato: uso solo l'audio "
                  "(vedi INSTALLARE_TELEMETRY_PARSER.md)", file=sys.stderr)
            _WARNED_NO_TP = True
        return None
    try:
        tp = telemetry_parser.Parser(str(path))
        imu = tp.normalized_imu()
        cam = f"{tp.camera} {tp.model}"
    except Exception as e:  # file senza telemetria o formato non supportato
        print(f"  [imu] {path.name}: non letta ({e})", file=sys.stderr)
        return None
    if not imu:
        return None
    t, g, a = [], [], []
    for s in imu:
        if s.get("gyro") is None or s.get("accl") is None:
            continue
        t.append(s["timestamp_ms"] / 1000.0)
        g.append(s["gyro"])
        a.append(s["accl"])
    if len(t) < 10:
        return None
    return cam, np.asarray(t), np.asarray(g, float), np.asarray(a, float)


def read_audio(path: Path, sr: int = 16000) -> np.ndarray:
    p = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1",
         "-ar", str(sr), "-f", "f32le", "-"],
        capture_output=True)
    return np.frombuffer(p.stdout, dtype=np.float32)


def bin_stat(t: np.ndarray, x: np.ndarray, n: int, fn) -> np.ndarray:
    """Applica fn (mean/std) ai campioni x raggruppati in bin da DT."""
    idx = np.clip((t / DT).astype(int), 0, n - 1)
    out = np.zeros(n)
    for i in range(n):
        v = x[idx == i]
        out[i] = fn(v) if len(v) > 1 else 0.0
    return out


def extract_features(clip: Clip) -> None:
    n = int(clip.duration / DT)
    if n < 2:
        return

    # --- IMU ---
    r = read_imu(clip.path)
    if r is not None:
        clip.camera, t, gyro, accl = r
        clip.imu_samples = len(t)
        gnorm = np.linalg.norm(gyro, axis=1)
        anorm = np.linalg.norm(accl, axis=1)
        # rotazione "sostenuta": mediana su 5 bin (2.5 s) per ignorare
        # le occhiate agli specchietti, che sono scatti brevi
        clip.feats["gyro"] = medfilt(bin_stat(t, gnorm, n, np.mean), 5)
        # vibrazioni / buche / frenate: variazione della norma dell'accelerazione
        clip.feats["accel"] = bin_stat(t, anorm, n, np.std)

    # --- Audio (energia = vento ~ velocita', + motore) ---
    if clip.a_ch:
        sr = 16000
        au = read_audio(clip.path, sr)
        if len(au) > sr:
            per = int(sr * DT)
            m = min(n, len(au) // per)
            rms = np.sqrt((au[: m * per].reshape(m, per) ** 2).mean(axis=1))
            db = 20 * np.log10(rms + 1e-6)
            clip.feats["audio"] = np.pad(db, (0, n - m), mode="edge")


def normalize_and_score(clips: list[Clip], weights: dict, edge_trim: float) -> None:
    """Normalizza ogni feature sull'insieme di tutte le clip (p10..p95)."""
    for name in weights:
        allv = [c.feats[name] for c in clips if name in c.feats]
        if not allv:
            continue
        cat = np.concatenate(allv)
        lo, hi = np.percentile(cat, 10), np.percentile(cat, 95)
        span = (hi - lo) or 1.0
        for c in clips:
            if name in c.feats:
                c.feats[name] = np.clip((c.feats[name] - lo) / span, 0, 1)

    trim = int(edge_trim / DT)
    for c in clips:
        have = {k: w for k, w in weights.items() if k in c.feats and w > 0}
        if not have:
            c.score = None
            continue
        tot = sum(have.values())
        n = len(next(iter(c.feats.values())))
        s = np.zeros(n)
        for k, w in have.items():
            s += (w / tot) * c.feats[k]
        if trim:  # inizio/fine clip: avvio, parcheggio, mani sulla camera
            s[:trim] = 0
            s[-trim:] = 0
        c.score = s


# ----------------------------------------------------------------------------
# Musica
# ----------------------------------------------------------------------------
def beat_times(music: Path):
    """(primo beat in secondi, beat relativi al primo)."""
    import librosa
    y, sr = librosa.load(str(music), sr=22050, mono=True)
    _, beats = librosa.beat.beat_track(y=y, sr=sr, units="time")
    beats = np.asarray(beats, float)
    if len(beats) < 8:
        raise SystemExit("ERRORE: nel brano non riesco a trovare un ritmo (beat). "
                         "Prova un altro brano oppure togli --music.")
    return float(beats[0]), beats - beats[0]


# ----------------------------------------------------------------------------
# Scelta dei momenti
# ----------------------------------------------------------------------------
def find_peaks(clips, win, min_gap, stop_below=None, max_peaks=None):
    """Momenti migliori (massimi dello score), mai vicini nella stessa clip."""
    n_w = max(1, int(round(win / DT)))
    cands = []  # (score, clip_idx, bin_start)
    for ci, c in enumerate(clips):
        if c.score is None or len(c.score) < n_w:
            continue
        cs = np.concatenate([[0], np.cumsum(c.score)])
        means = (cs[n_w:] - cs[:-n_w]) / n_w
        cands += [(float(m), ci, i) for i, m in enumerate(means)]
    cands.sort(reverse=True)

    excl = int(round((win + min_gap) / DT))
    taken: dict[int, list[int]] = {}
    peaks = []
    for sc, ci, i in cands:
        if stop_below is not None and sc < stop_below:
            break
        if max_peaks and len(peaks) >= max_peaks:
            break
        lst = taken.setdefault(ci, [])
        if any(abs(i - j) < excl for j in lst):
            continue
        lst.append(i)
        peaks.append({"score": sc, "clip": ci, "centre": i * DT + win / 2})
    return peaks


def assign_durations(peaks, min_cut, max_cut, variety, seed):
    """Durata per momento: i momenti migliori durano di piu', piu' una variazione casuale."""
    if not peaks:
        return
    rng = np.random.default_rng(seed)
    sc = np.array([p["score"] for p in peaks])
    lo, hi = (np.percentile(sc, 25), np.percentile(sc, 90)) if len(sc) >= 4 else (sc.min(), sc.max())
    for p in peaks:
        f = 0.5 if hi - lo < 1e-9 else float(np.clip((p["score"] - lo) / (hi - lo), 0, 1))
        d = min_cut + f * (max_cut - min_cut)
        d *= 1 + variety * rng.uniform(-0.5, 0.5)
        p["dur"] = float(np.clip(d, min_cut, max_cut))


def assemble(clips, peaks, beats, length, order, min_cut):
    """Ordina i momenti e li trasforma in tagli (con durata esatta)."""
    if order == "build":  # energia crescente verso la fine
        peaks = sorted(peaks, key=lambda p: p["score"])
    else:  # ordine di ripresa
        peaks = sorted(peaks, key=lambda p: (p["clip"], p["centre"]))

    edit, elapsed, idx = [], 0.0, 0
    period = float(np.median(np.diff(beats))) if beats is not None else None
    min_beats = 2 * max(1, int(np.ceil(min_cut / (2 * period)))) if beats is not None else 0
    for p in peaks:
        if length and elapsed >= length:
            break
        c = clips[p["clip"]]
        if beats is not None:
            nb = max(min_beats, 2 * int(round(p["dur"] / (2 * period))))  # numero pari di beat
            if idx + nb >= len(beats):
                break  # la musica e' finita
            d = float(beats[idx + nb] - beats[idx])
            idx += nb
        else:
            d = round(p["dur"], 2)
        if d > c.duration:
            continue
        start = min(max(0.0, p["centre"] - d / 2), c.duration - d)
        edit.append({"clip": p["clip"], "start": round(start, 3), "dur": d,
                     "score": round(p["score"], 3)})
        elapsed += d
    return edit


def frame_bounds(durs, fps: float) -> list[int]:
    """Posizioni (in fotogrammi) dei tagli, calcolate sul tempo cumulato esatto
    cosi' gli arrotondamenti non si accumulano e i tagli restano sui beat."""
    cum = np.concatenate([[0.0], np.cumsum(durs)])
    return [int(round(x * fps)) for x in cum]


# ----------------------------------------------------------------------------
# Export
# ----------------------------------------------------------------------------
def _name(out: Path, ext: str) -> Path:
    return out.parent / (out.name + ext)


def _sec(fr: Fraction) -> str:
    return "0s" if fr == 0 else f"{fr.numerator}/{fr.denominator}s"


def export_fcpxml(clips, edit, bounds, seq_fps: Fraction, music, music_offset,
                  out: Path, clip_db: float, music_db: float) -> Path:
    """FCPXML 1.8: ogni clip porta con se' il proprio audio (motore); la musica
    e' collegata alla prima clip."""
    root = ET.Element("fcpxml", version="1.8")
    res = ET.SubElement(root, "resources")
    counter = [0]

    def new_id():
        counter[0] += 1
        return f"r{counter[0]}"

    formats: dict = {}

    def fmt_of(c: Clip) -> str:
        key = (c.width, c.height, c.fps_frac)
        if key not in formats:
            fid = new_id()
            ET.SubElement(res, "format", id=fid, frameDuration=_sec(1 / c.fps_frac),
                          width=str(c.width), height=str(c.height))
            formats[key] = fid
        return formats[key]

    assets: dict = {}

    def asset_of(c: Clip) -> str:
        if c.path not in assets:
            fid = fmt_of(c)
            aid = new_id()
            frames = int(c.duration * c.fps)
            a = ET.SubElement(res, "asset", id=aid, name=c.path.stem, start="0s",
                              duration=_sec(Fraction(frames) / c.fps_frac),
                              hasVideo="1", format=fid, src=c.path.resolve().as_uri())
            if c.a_ch:
                a.set("hasAudio", "1")
                a.set("audioSources", "1")
                a.set("audioChannels", str(c.a_ch))
                a.set("audioRate", str(c.a_rate if c.a_rate in FCPX_AUDIO_RATES else 48000))
            assets[c.path] = aid
        return assets[c.path]

    first = clips[edit[0]["clip"]]
    seq_fmt = fmt_of(first)
    total_frames = bounds[-1]

    music_id = None
    if music:
        m_dur, m_sr, m_ch = probe_audio(music)
        music_id = new_id()
        ET.SubElement(res, "asset", id=music_id, name=music.stem, start="0s",
                      duration=_sec(Fraction(int(m_dur * m_sr), m_sr)),
                      hasAudio="1", audioSources="1", audioChannels=str(m_ch),
                      audioRate=str(m_sr if m_sr in FCPX_AUDIO_RATES else 48000),
                      src=music.resolve().as_uri())

    lib = ET.SubElement(root, "library")
    ev = ET.SubElement(lib, "event", name="moto_autocut")
    pr = ET.SubElement(ev, "project", name=out.name)
    seq = ET.SubElement(pr, "sequence", format=seq_fmt,
                        duration=_sec(Fraction(total_frames) / seq_fps), tcStart="0s",
                        tcFormat="NDF", audioLayout="stereo", audioRate="48k")
    spine = ET.SubElement(seq, "spine")

    for k, e in enumerate(edit):
        c = clips[e["clip"]]
        d_seq = bounds[k + 1] - bounds[k]
        d_clip = max(1, int(round(d_seq / float(seq_fps) * c.fps)))
        asset_frames = int(c.duration * c.fps)
        s_clip = min(int(round(e["start"] * c.fps)), max(0, asset_frames - d_clip))
        start_str = _sec(Fraction(s_clip) / c.fps_frac)
        ac = ET.SubElement(spine, "asset-clip", ref=asset_of(c),
                           offset=_sec(Fraction(bounds[k]) / seq_fps), name=c.path.stem,
                           start=start_str, duration=_sec(Fraction(d_seq) / seq_fps),
                           tcFormat="NDF")
        if clip_db:
            ET.SubElement(ac, "adjust-volume", amount=f"{clip_db:g}dB")
        if k == 0 and music_id:
            mc = ET.SubElement(ac, "asset-clip", ref=music_id, lane="-1",
                               offset=start_str, name=music.stem,
                               start=_sec(Fraction(int(music_offset * m_sr), m_sr)),
                               duration=_sec(Fraction(total_frames) / seq_fps))
            if music_db:
                ET.SubElement(mc, "adjust-volume", amount=f"{music_db:g}dB")

    ET.indent(root)
    path = _name(out, ".fcpxml")
    path.write_text('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n'
                    + ET.tostring(root, encoding="unicode"), encoding="utf-8")
    return path


def export_otio(clips, edit, bounds, seq_fps: float, music, music_offset, out: Path):
    import opentimelineio as otio
    from opentimelineio.opentime import RationalTime, TimeRange

    def ref_for(path: Path, dur: float, rate: float):
        return otio.schema.ExternalReference(
            target_url=path.resolve().as_uri(),
            available_range=TimeRange(RationalTime(0, rate), RationalTime(round(dur * rate), rate)))

    tl = otio.schema.Timeline(name=out.name)
    v = otio.schema.Track(name="Video", kind=otio.schema.TrackKind.Video)
    tl.tracks.append(v)
    cuts = []
    for k, e in enumerate(edit):
        c = clips[e["clip"]]
        dsec = (bounds[k + 1] - bounds[k]) / seq_fps
        sr = TimeRange(RationalTime(round(e["start"] * c.fps), c.fps),
                       RationalTime(round(dsec * c.fps), c.fps))
        cuts.append((c, sr))
        v.append(otio.schema.Clip(name=c.path.stem,
                                  media_reference=ref_for(c.path, c.duration, c.fps),
                                  source_range=sr))

    written = []
    try:  # XML FCP7: solo video (l'audio passa meglio dal .fcpxml)
        p = _name(out, ".xml")
        otio.adapters.write_to_file(tl, str(p), adapter_name="fcp_xml")
        written.append(p)
    except Exception as ex:
        print(f"  (export XML saltato: {ex}; installa otio-fcp-adapter)", file=sys.stderr)

    a1 = otio.schema.Track(name="Audio clip", kind=otio.schema.TrackKind.Audio)
    tl.tracks.append(a1)
    for c, sr in cuts:
        if c.a_ch:
            a1.append(otio.schema.Clip(name=c.path.stem,
                                       media_reference=ref_for(c.path, c.duration, c.fps),
                                       source_range=sr))
        else:
            a1.append(otio.schema.Gap(source_range=TimeRange(RationalTime(0, c.fps), sr.duration)))
    if music:
        m_dur, _, _ = probe_audio(music)
        a2 = otio.schema.Track(name="Musica", kind=otio.schema.TrackKind.Audio)
        tl.tracks.append(a2)
        a2.append(otio.schema.Clip(
            name=music.stem, media_reference=ref_for(music, m_dur, seq_fps),
            source_range=TimeRange(RationalTime(round(music_offset * seq_fps), seq_fps),
                                   RationalTime(bounds[-1], seq_fps))))
    p = _name(out, ".otio")
    otio.adapters.write_to_file(tl, str(p))
    written.append(p)
    return written


def render_audio(clips, edit, bounds, seq_fps: float, music, music_offset,
                 out: Path, clip_db: float, music_db: float) -> Path | None:
    """Audio finale gia' mixato (audio delle clip + musica) con ffmpeg.
    Serve come rete di sicurezza: si trascina in Resolve e si sente sempre."""
    cmd = ["ffmpeg", "-v", "error", "-y"]
    filt, labels = [], []
    for k, e in enumerate(edit):
        c = clips[e["clip"]]
        dur = (bounds[k + 1] - bounds[k]) / seq_fps
        if c.a_ch:
            cmd += ["-ss", f"{e['start']:.3f}", "-t", f"{dur:.3f}", "-i", str(c.path)]
        else:
            cmd += ["-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
        # apad+atrim: ogni pezzo dura ESATTAMENTE quanto il taglio (niente sfasamenti)
        filt.append(f"[{k}:a:0]aresample=48000,aformat=channel_layouts=stereo,"
                    f"apad,atrim=0:{dur:.6f},asetpts=N/SR/TB[a{k}]")
        labels.append(f"[a{k}]")
    n = len(edit)
    filt.append("".join(labels) + f"concat=n={n}:v=0:a=1,volume={clip_db}dB[c]")
    total = bounds[-1] / seq_fps
    if music:
        cmd += ["-ss", f"{music_offset:.3f}", "-t", f"{total:.3f}", "-i", str(music)]
        filt.append(f"[{n}:a:0]aresample=48000,aformat=channel_layouts=stereo,"
                    f"volume={music_db}dB[m]")
        filt.append("[c][m]amix=inputs=2:duration=first:dropout_transition=0,"
                    "volume=2,alimiter=limit=0.95[out]")
    else:
        filt.append("[c]alimiter=limit=0.95[out]")
    path = _name(out, "_audio.wav")
    cmd += ["-filter_complex", ";".join(filt), "-map", "[out]", "-ar", "48000",
            "-ac", "2", "-c:a", "pcm_s16le", str(path)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("  (audio mixato non creato: " + r.stderr.strip().splitlines()[-1] + ")", file=sys.stderr)
        return None
    return path


# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", type=Path, help="cartella con le clip (oppure un singolo video)")
    ap.add_argument("--music", type=Path, help="brano su cui sincronizzare i tagli")
    ap.add_argument("-o", "--out", type=Path, default=Path("montaggio"),
                    help="nome file output (senza estensione)")
    ap.add_argument("--length", type=float, default=None,
                    help="durata circa desiderata in secondi. Se omesso: nessun limite, "
                         "si usano tutti i momenti con score >= --min-score")
    ap.add_argument("--min-score", type=float, default=0.5,
                    help="senza --length: soglia (0-1) per tenere un momento (default 0.5)")
    ap.add_argument("--min-cut", type=float, default=1.5, help="durata minima di un taglio (s)")
    ap.add_argument("--max-cut", type=float, default=6.0, help="durata massima di un taglio (s)")
    ap.add_argument("--variety", type=float, default=0.8,
                    help="variazione casuale delle durate: 0 = solo in base allo score, 1 = molta")
    ap.add_argument("--seed", type=int, default=7, help="cambia numero per un'altra variante")
    ap.add_argument("--min-gap", type=float, default=3.0,
                    help="distanza minima tra due scelte della stessa clip (s)")
    ap.add_argument("--edge-trim", type=float, default=3.0, help="secondi scartati a inizio/fine di ogni clip")
    ap.add_argument("--order", choices=["chrono", "build"], default="chrono",
                    help="chrono = ordine di ripresa; build = energia crescente")
    ap.add_argument("--music-db", type=float, default=-8.0, help="volume della musica in dB (default -8)")
    ap.add_argument("--clip-db", type=float, default=0.0, help="volume dell'audio delle clip in dB (default 0)")
    ap.add_argument("--no-wav", action="store_true", help="non creare l'audio mixato .wav")
    ap.add_argument("--w-gyro", type=float, default=0.35)
    ap.add_argument("--w-accel", type=float, default=0.30)
    ap.add_argument("--w-audio", type=float, default=0.35)
    ap.add_argument("--analyze-only", action="store_true", help="solo diagnosi, niente export")
    args = ap.parse_args()

    if args.min_cut <= 0 or args.max_cut < args.min_cut:
        raise SystemExit("ERRORE: --min-cut deve essere > 0 e non maggiore di --max-cut.")

    missing = [x for x in ("ffmpeg", "ffprobe") if shutil.which(x) is None]
    if missing:
        raise SystemExit(
            "ERRORE: non trovo " + " e ".join(missing) + " sul computer.\n"
            "Su Windows: apri il Prompt dei comandi e scrivi\n"
            "    winget install Gyan.FFmpeg\n"
            "poi CHIUDI e riapri il Prompt dei comandi e riprova.")
    if not args.folder.exists():
        raise SystemExit(
            f"ERRORE: il percorso '{args.folder}' non esiste (controlla lettera del disco e nome).\n"
            "Puoi indicare una cartella con i video oppure un singolo video, ad esempio\n"
            '    python moto_autocut.py "C:\\Users\\TuoNome\\Videos\\Giro" --analyze-only\n'
            '    python moto_autocut.py "C:\\Users\\TuoNome\\Videos\\VID_001.mp4" --analyze-only\n'
            "(non scrivere un backslash finale prima delle virgolette).")
    if args.music and not args.music.is_file():
        raise SystemExit(
            f"ERRORE: il file musicale '{args.music}' non esiste.\n"
            "Usa il percorso vero del tuo brano, oppure togli --music.")

    def is_proxy(p: Path) -> bool:
        n = p.stem.lower()  # i file LRV_* della camera sono copie a bassa risoluzione
        return n.startswith("lrv_") or "_lrv" in n

    if args.folder.is_file():  # un solo video
        files = [args.folder]
    else:
        files = sorted(p for p in args.folder.iterdir()
                       if p.suffix.lower() in VIDEO_EXT and not p.name.startswith(".")
                       and not is_proxy(p))
    if not files:
        raise SystemExit(f"ERRORE: nessun video (mp4/mov/mkv) trovato in '{args.folder}'.")

    clips: list[Clip] = []
    for p in files:
        print(f"Analizzo {p.name} ...")
        try:
            c = probe(p)
        except Exception as e:
            print(f"  salto il file, non riesco a leggerlo ({e})", file=sys.stderr)
            continue
        extract_features(c)
        print(f"  {c.duration:.0f}s @ {c.fps:.2f}fps | audio: {'si' if c.a_ch else 'NO'} | "
              f"camera: {c.camera} | campioni IMU: {c.imu_samples} | "
              f"feature: {', '.join(c.feats) or 'nessuna'}")
        clips.append(c)
    if not clips:
        raise SystemExit("ERRORE: nessun video leggibile.")

    weights = {"gyro": args.w_gyro, "accel": args.w_accel, "audio": args.w_audio}
    normalize_and_score(clips, weights, args.edge_trim)

    if args.analyze_only:
        no_imu = [c.path.name for c in clips if "gyro" not in c.feats]
        no_audio = [c.path.name for c in clips if not c.a_ch]
        print("\nClip senza IMU:", no_imu or "nessuna")
        print("Clip senza audio:", no_audio or "nessuna")
        return
    if all(c.score is None for c in clips):
        raise SystemExit("ERRORE: nessun segnale calcolabile (ne' IMU ne' audio).")

    # --- musica ---
    offset, beats = (beat_times(args.music) if args.music else (0.0, None))

    # --- momenti e durate ---
    base = (args.min_cut + args.max_cut) / 2
    if args.length:
        peaks = find_peaks(clips, base, args.min_gap, max_peaks=int(args.length / args.min_cut) + 10)
    else:
        peaks = find_peaks(clips, base, args.min_gap, stop_below=args.min_score)
    if not peaks:
        raise SystemExit(f"Nessun momento con score >= {args.min_score}: prova ad abbassare --min-score "
                         "(per esempio 0.35) oppure indica una durata con --length.")
    assign_durations(peaks, args.min_cut, args.max_cut, args.variety, args.seed)
    if args.length:  # i migliori finche' non si raggiunge la durata
        chosen, acc = [], 0.0
        for p in peaks:
            chosen.append(p)
            acc += p["dur"]
            if acc >= args.length:
                break
        peaks = chosen

    edit = assemble(clips, peaks, beats, args.length, args.order, args.min_cut)
    if not edit:
        raise SystemExit("ERRORE: nessun taglio utilizzabile.")

    seq_fps = clips[edit[0]["clip"]].fps_frac
    bounds = frame_bounds([e["dur"] for e in edit], float(seq_fps))
    total = bounds[-1] / float(seq_fps)

    written = [export_fcpxml(clips, edit, bounds, seq_fps, args.music, offset, args.out,
                             args.clip_db, args.music_db)]
    written += export_otio(clips, edit, bounds, float(seq_fps), args.music, offset, args.out)
    if not args.no_wav:
        w = render_audio(clips, edit, bounds, float(seq_fps), args.music, offset,
                         args.out, args.clip_db, args.music_db)
        if w:
            written.append(w)

    durs = [e["dur"] for e in edit]
    print(f"\nMontaggio: {len(edit)} tagli, {total:.1f}s "
          f"(taglio piu' corto {min(durs):.1f}s, piu' lungo {max(durs):.1f}s, medio {np.mean(durs):.1f}s)")
    if beats is not None and len(edit) < len(peaks):
        print(f"  La musica e' finita: usati {len(edit)} momenti su {len(peaks)} trovati.")
    for e in edit:
        print(f"  {clips[e['clip']].path.name:34s} {e['start']:7.1f}s +{e['dur']:.2f}s  score {e['score']:.2f}")
    print("\nFile creati:")
    for w in written:
        print("  " + str(w))
    print("In Resolve: File -> Import -> Timeline -> scegli il file .fcpxml")


if __name__ == "__main__":
    main()