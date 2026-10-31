"""Readability scores (English): Flesch, Flesch-Kincaid, Gunning Fog, SMOG, Coleman-Liau, ARI."""
from __future__ import annotations

import math
import os
import re

from ..core import Tool, ToolError, add_net_args, issue, load_page, read_text_file, sort_issues

_WORDS = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)*")


def syllables(word):
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return 0
    if len(w) <= 3:
        return 1
    w = re.sub(r"(?:[^laeiouy]es|ed|[^laeiouy]e)$", "", w)
    w = re.sub(r"^y", "", w)
    return max(1, len(re.findall(r"[aeiouy]{1,2}", w)))


def split_sentences(text):
    parts = re.split(r"(?<=[.!?])\s+|\n{2,}", text.strip())
    return [p for p in parts if re.search(r"[A-Za-z]", p)]


def band(flesch):
    for limit, label in ((90, "very easy (5th grade)"), (80, "easy (6th grade)"), (70, "fairly easy (7th grade)"),
                         (60, "plain English (8th-9th grade)"), (50, "fairly difficult (10th-12th grade)"),
                         (30, "difficult (college)")):
        if flesch >= limit:
            return label
    return "very difficult (graduate)"


def analyze(text):
    sentences = split_sentences(text)
    words = _WORDS.findall(text)
    if len(words) < 10 or not sentences:
        raise ToolError("need at least ~10 words of English text to score readability")
    W, S = len(words), len(sentences)
    syl = [syllables(w) for w in words]
    Y = sum(syl)
    complex_w = sum(1 for s in syl if s >= 3)
    letters = sum(len(re.sub(r"[^A-Za-z]", "", w)) for w in words)
    flesch = 206.835 - 1.015 * (W / S) - 84.6 * (Y / W)
    fk = 0.39 * (W / S) + 11.8 * (Y / W) - 15.59
    fog = 0.4 * ((W / S) + 100.0 * complex_w / W)
    smog = 1.043 * math.sqrt(complex_w * 30.0 / S) + 3.1291
    L, Sn = letters / W * 100, S / W * 100
    cli = 0.0588 * L - 0.296 * Sn - 15.8
    ari = 4.71 * (letters / W) + 0.5 * (W / S) - 21.43
    long_sent = [s.strip()[:80] for s in sentences if len(_WORDS.findall(s)) > 25]
    issues = []
    if flesch < 50:
        issues.append(issue("warning", "HARD_TO_READ", "Flesch %.0f is %s; most web audiences read comfortably at 60+." % (flesch, band(flesch))))
    if W / S > 22:
        issues.append(issue("warning", "LONG_SENTENCES", "Average sentence is %.1f words; aim for 15-20." % (W / S)))
    if long_sent:
        issues.append(issue("info", "VERY_LONG_SENTENCES", "%d sentences exceed 25 words." % len(long_sent), examples=long_sent[:3]))
    if S < 30:
        issues.append(issue("info", "SMOG_SMALL_SAMPLE", "SMOG is designed for 30+ sentences; treat it as rough here."))
    return {
        "words": W, "sentences": S, "syllables": Y, "avg_words_per_sentence": round(W / S, 1),
        "avg_syllables_per_word": round(Y / W, 2), "complex_words_pct": round(100.0 * complex_w / W, 1),
        "reading_time_min": round(W / 238.0, 1),
        "flesch_reading_ease": round(flesch, 1), "flesch_band": band(flesch),
        "flesch_kincaid_grade": round(fk, 1), "gunning_fog": round(fog, 1), "smog": round(smog, 1),
        "coleman_liau": round(cli, 1), "automated_readability_index": round(ari, 1),
        "note": "English-only heuristics (syllables are estimated).", "issues": sort_issues(issues),
    }


def _args(p):
    p.add_argument("target", nargs="?", help="URL, .html file or text file")
    p.add_argument("--text")
    add_net_args(p)


def _run(a):
    if a.text:
        text = a.text
    elif not a.target:
        raise ToolError("give a URL/file or --text")
    elif os.path.isfile(a.target) and not a.target.lower().endswith((".html", ".htm")):
        text = read_text_file(a.target)
    else:
        text = load_page(a.target, a.ua, a.timeout)[0].main_text
    return analyze(text)


TOOLS = [Tool("readability", "SEO / Content & keywords", "Readability scores: Flesch, Flesch-Kincaid, Fog, SMOG, Coleman-Liau, ARI + reading time", _args, _run)]
