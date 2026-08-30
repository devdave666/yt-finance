"""Build an .ass caption file.

  explainer  word-by-word REVEAL: 2-3 words on screen at a time, each word
             pops in exactly as it's spoken (the newest word bigger + green,
             the rest white); the group clears at a clause break and the next
             group starts. This is the high-retention short-form style -- you
             can't skim ahead, and every new word is a little motion hit.
  skit       white ALL-CAPS bold on a black pill, one cue per line
  title      one persistent meme-style caption
  none       none

Word timing is a syllable-weighted split of each beat's measured speech span
(narration.json). Not frame-accurate, but locked to the voice closely enough
that the reveal tracks the delivery.
"""
from __future__ import annotations

import json
import re
import textwrap

from . import config

_GREEN = config.CAP_SPOKEN
_WHITE = "&H00FFFFFF"

_STYLES = {
    # name -> (Font, Size, BorderStyle, Outline, Outline colour, Align, MarginV, Bold)
    "explainer": ("Arial", 96, 1, 6, config.CAP_OUTLINE, 8, 360, -1),
    "skit":      ("Arial", 66, 3, 6, "&H00000000", 8, 320, -1),
    "title":     ("Arial", 58, 3, 8, "&H00FFFFFF", 8, 250, -1),
}


def _header(fmt, style_name):
    w, h = config.canvas(fmt)
    font, size, border, outline, oc, align, marginv, bold = _STYLES[style_name]
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{font},{size},{_WHITE},{_WHITE},{oc},{oc},{bold},0,0,0,100,100,0,0,{border},{outline},1,{align},60,60,{marginv},1

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
    n = len(re.findall(r"[aeiouy]+", w.lower()))
    if n > 1 and w.lower().endswith("e"):
        n -= 1
    return max(1, n)


def _key(w: str) -> bool:
    """Numbers, money, percentages and shouted words stay accent-coloured."""
    return bool(re.search(r"\d", w)) or w.isupper() and len(w) > 1 or "$" in w or "%" in w


def _word_times(words: list[str], start: float, end: float) -> list[tuple[float, float]]:
    wt = []
    for i, w in enumerate(words):
        x = _syllables(w) + 0.5
        if i < len(words) - 1 and w and w[-1] in ",;:.?!…—-":
            x += 2.0 if w[-1] in ".?!" else 1.2
        wt.append(x)
    span = max(end - start, 0.3)
    tot = sum(wt)
    out, acc = [], 0.0
    for i, x in enumerate(wt):
        w0 = start + span * acc / tot
        acc += x
        w1 = end if i == len(words) - 1 else start + span * acc / tot
        out.append((w0, w1))
    return out


def _groups(words: list[str]) -> list[tuple[int, int]]:
    """Split into runs of ~2-3 words; break after hard punctuation; keep a
    number and the words right around it together."""
    out, lo = [], 0
    for i, w in enumerate(words):
        n = i - lo + 1
        hard = w and w[-1] in ".?!,;:"
        near_num = _key(w) or (i + 1 < len(words) and _key(words[i + 1]))
        if i == len(words) - 1:
            out.append((lo, i + 1))
        elif hard or (n >= 3 and not near_num) or n >= 4:
            out.append((lo, i + 1))
            lo = i + 1
    return out


def _reveal(text: str, start: float, end: float) -> list[str]:
    words = [w for w in text.split() if w]
    if not words:
        return []
    times = _word_times(words, start, end)
    events = []
    for lo, hi in _groups(words):
        g_end = times[hi - 1][1] + 0.10
        for j in range(lo, hi):
            e_start = times[j][0]
            e_end = g_end if j == hi - 1 else times[j + 1][0]
            shown = []
            for k in range(lo, j + 1):
                w = words[k]
                if k == j:                       # newest word: green + a quick pop
                    shown.append(r"{\c" + _GREEN + r"\fscx122\fscy122"
                                 r"\t(0,90,\fscx100\fscy100)}" + w + r"{\c" + _WHITE + "}")
                elif _key(w):
                    shown.append(r"{\c" + _GREEN + "}" + w + r"{\c" + _WHITE + "}")
                else:
                    shown.append(w)
            body = r"{\fad(50,0)}" + " ".join(shown)
            events.append(f"Dialogue: 0,{_ts(e_start)},{_ts(max(e_end, e_start + 0.12))},"
                          f"Cap,,0,0,0,,{body}")
    return events


def build(script, out_path):
    style = script.caption_style
    if style == "none":
        return None

    narration = json.loads((script.build_dir / "narration.json").read_text())
    beat_by_id = {b.id: b for b in script.beats}
    lines = [_header(script.fmt, style)]

    if style == "title":
        lines.append(f"Dialogue: 0,{_ts(0)},{_ts(narration['total_s'])},Cap,,0,0,0,,"
                     f"{{\\fad(150,0)}}{_wrap(script.title_card or script.title, 24)}")
        out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return out_path

    t = 0.0
    for entry in narration["beats"]:
        d = entry["duration_s"]
        beat = beat_by_id[entry["id"]]
        if beat.is_live or not beat.say:
            t += d
            continue
        s0 = float(entry.get("speech_start_s", 0.0) or 0.0)
        s1 = float(entry.get("speech_end_s", d) or d)
        if not (0.0 <= s0 < s1 <= d + 0.05):
            s0, s1 = 0.0, d
        if style == "skit":
            lines.append(f"Dialogue: 0,{_ts(t)},{_ts(t + max(d - 0.04, 0.2))},Cap,,0,0,0,,"
                         f"{{\\fad(90,60)}}{_wrap(beat.say.upper(), 22)}")
        else:
            lines.extend(_reveal(beat.say, t + s0, t + min(s1 + 0.10, d)))
        t += d

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path
