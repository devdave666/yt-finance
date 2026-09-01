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


def _reveal(text: str, start: float, speech_end: float, hold_until: float) -> list[str]:
    """Word-by-word reveal between `start` and `speech_end`; the last group then
    holds (still) until `hold_until`. NOTHING is emitted past `hold_until` -- that
    is the next beat's caption start, so there's never two caption lines stacked.

    Every word's onset is clamped into one monotonically non-decreasing list
    FIRST, and each event's end is always exactly the NEXT word's (already-
    clamped) onset -- never independently reclamped. A word's onset used to
    be capped at `hold_until - 0.08` while the PRECEDING word's end was capped
    at plain `hold_until`; whenever a word's natural time landed in that 80ms
    gap (common -- trimmed TTS speech usually fills most of a beat) the new
    caption started before the old one's end, i.e. two lines on screen at
    once. Sharing one clamped boundary between consecutive events makes that
    structurally impossible now.
    """
    words = [w for w in text.split() if w]
    if not words:
        return []
    raw_times = _word_times(words, start, speech_end)
    groups = _groups(words)

    eps = 0.08
    starts, prev = [], start
    for w0, _w1 in raw_times:
        s = max(min(w0, hold_until - eps), prev)
        # hard ceiling AFTER the monotonic floor: if `prev` alone has already
        # cascaded past hold_until-eps (several words bunched near the
        # boundary), don't let it punch through hold_until itself -- clamp
        # back down. Still monotonic: once `s` sits at the ceiling, every
        # later word's max(..., prev) keeps it pinned there, never below.
        s = min(s, hold_until)
        starts.append(s)
        prev = s

    events = []
    for gi, (lo, hi) in enumerate(groups):
        for j in range(lo, hi):
            e_start = starts[j]
            e_end = starts[hi] if j == hi - 1 and hi < len(words) else \
                    (hold_until if j == hi - 1 else starts[j + 1])
            if e_end <= e_start:
                continue          # degenerate (two words clamped to the same instant)
            shown = []
            for k in range(lo, j + 1):
                w = words[k]
                if k == j:                       # newest word: green + a quick scale-in
                    shown.append(r"{\c" + _GREEN + r"\fscx118\fscy118"
                                 r"\t(0,80,\fscx100\fscy100)}" + w + r"{\c" + _WHITE + "}")
                elif _key(w):
                    shown.append(r"{\c" + _GREEN + "}" + w + r"{\c" + _WHITE + "}")
                else:
                    shown.append(w)
            # fade ONLY on the first frame of a group; within a group the line
            # is solid and only the new word animates (no per-word flicker)
            fade = r"{\fad(70,0)}" if j == lo else ""
            events.append(f"Dialogue: 0,{_ts(e_start)},{_ts(e_end)},"
                          f"Cap,,0,0,0,,{fade}" + " ".join(shown))
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
            # reveal tracks the voice (s0..s1); the last group then holds to the
            # beat boundary minus a hair, so it clears before the next beat's
            # first word fades in (no cross-fade collision)
            lines.extend(_reveal(beat.say, t + s0, t + s1, t + d - 0.03))
        t += d

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path
