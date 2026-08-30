"""Build an .ass caption file.

  explainer  one cue per beat, whole sentence, big bold on a soft card, words
             light up karaoke-style across the beat's duration, pop-in fade
  skit       white ALL-CAPS bold on a black pill, upper third, one cue per line
  title      one persistent meme-style caption for the whole video
  none       no captions

Word timing is approximate (beat duration split across words by length) --
per-beat TTS gives us the beat length for free but not word timestamps, and
approximate karaoke still reads as "dynamic captions".
"""
from __future__ import annotations

import json
import re
import textwrap

from . import config

# name -> (Font, Size, Primary[spoken], Secondary[pending], BorderStyle, Outline,
#          Outline/box colour, Alignment, MarginV, Bold)
_STYLES = {
    "explainer": ("Arial", 82, config.CAP_SPOKEN, config.CAP_PENDING, 1, 5, config.CAP_OUTLINE, 8, 300, -1),
    "skit":      ("Arial", 66, "&H00FFFFFF", "&H00FFFFFF", 3, 6, "&H00000000", 8, 320, -1),
    "title":     ("Arial", 58, "&H00000000", "&H00000000", 3, 8, "&H00FFFFFF", 8, 250, -1),
}


def _header(fmt, style_name):
    w, h = config.canvas(fmt)
    font, size, primary, secondary, border, outline, back, align, marginv, bold = _STYLES[style_name]
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{font},{size},{primary},{secondary},{back},{back},{bold},0,0,0,100,100,0,0,{border},{outline},1,{align},70,70,{marginv},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _ts(t: float) -> str:
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


def _wrap(text: str, width: int) -> str:
    return "\\N".join(textwrap.wrap(text, width=width)) or text


def _syllables(w: str) -> int:
    groups = re.findall(r"[aeiouy]+", w.lower())
    n = len(groups)
    if n > 1 and w.lower().endswith("e"):
        n -= 1
    return max(1, n)


def _karaoke(text: str, dur: float) -> str:
    """`{\\kNN}` per word, centiseconds, summing to dur.

    Time is split by a rough speech-length model -- syllable count plus a
    lingering weight on words that end a clause -- not raw letter count, so the
    highlight doesn't race ahead through the setup and then wait on the pause.
    """
    words = text.split()
    if not words:
        return text
    weights = []
    for i, w in enumerate(words):
        wt = _syllables(w) + 0.5
        if i < len(words) - 1:
            tail = w[-1]
            if tail in ",;:":
                wt += 1.5
            elif tail in '.?!…—-"\'':
                wt += 2.5
        weights.append(wt)
    total_cs = max(int(dur * 100), len(words))
    tw = sum(weights)
    out, spent = [], 0
    for i, (w, wt) in enumerate(zip(words, weights)):
        cs = total_cs - spent if i == len(words) - 1 else max(1, round(total_cs * wt / tw))
        spent += cs
        out.append(f"{{\\k{cs}}}{w}")
    return " ".join(out)


def build(script, out_path):
    style = script.caption_style
    if style == "none":
        return None

    narration = json.loads((script.build_dir / "narration.json").read_text())
    beat_by_id = {b.id: b for b in script.beats}
    lines = [_header(script.fmt, style)]

    if style == "title":
        text = _wrap(script.title_card or script.title, 24)
        lines.append(f"Dialogue: 0,{_ts(0)},{_ts(narration['total_s'])},Cap,,0,0,0,,"
                     f"{{\\fad(150,0)}}{text}")
        out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return out_path

    t = 0.0
    for entry in narration["beats"]:
        d = entry["duration_s"]
        beat = beat_by_id[entry["id"]]
        if beat.is_live or not beat.say:
            t += d
            continue
        # speech span within the clip -- keeps the highlight locked to the voice
        s0 = float(entry.get("speech_start_s", 0.0) or 0.0)
        s1 = float(entry.get("speech_end_s", d) or d)
        if not (0.0 <= s0 < s1 <= d + 0.05):
            s0, s1 = 0.0, d
        if style == "skit":
            start, end = t, t + max(d - 0.04, 0.2)
            body = f"{{\\fad(90,60)}}{_wrap(beat.say.upper(), 22)}"
        else:
            start = t + s0
            end = t + min(s1 + 0.12, d)
            body = f"{{\\fad(120,70)}}{_karaoke(beat.say, max(s1 - s0, 0.3))}"
        lines.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},Cap,,0,0,0,,{body}")
        t += d

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path
