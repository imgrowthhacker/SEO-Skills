"""SERP snippet preview: estimates pixel widths using Arial metrics."""
from __future__ import annotations

import html as _html

from ..core import Tool, ToolError, add_net_args, issue, load_page, sort_issues

# Arial advance widths per 1000 em.
_W = {" ": 278, "!": 278, '"': 355, "#": 556, "$": 556, "%": 889, "&": 667, "'": 191, "(": 333, ")": 333,
      "*": 389, "+": 584, ",": 278, "-": 333, ".": 278, "/": 278, ":": 278, ";": 278, "<": 584, "=": 584,
      ">": 584, "?": 556, "@": 1015, "[": 278, "\\": 278, "]": 278, "^": 469, "_": 556, "`": 333,
      "{": 334, "|": 260, "}": 334, "~": 584, "–": 556, "—": 1000, "…": 1000, "|": 260}
for _c in "0123456789":
    _W[_c] = 556
for _c, _w in zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ",
                  [667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778, 667, 778, 722,
                   667, 611, 722, 667, 944, 667, 667, 611]):
    _W[_c] = _w
for _c, _w in zip("abcdefghijklmnopqrstuvwxyz",
                  [556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556, 556, 556, 333,
                   500, 278, 556, 500, 722, 500, 500, 500]):
    _W[_c] = _w

TITLE_LIMIT_PX = 600
DESC_LIMIT_PX = 920


def text_width_px(text, font_px=20):
    total = 0
    for ch in text:
        w = _W.get(ch)
        if w is None:
            w = 1000 if ord(ch) >= 0x2E80 else 556
        total += w
    return total * font_px / 1000.0


def truncate_px(text, limit_px, font_px):
    if text_width_px(text, font_px) <= limit_px:
        return text, False
    out = ""
    for ch in text:
        if text_width_px(out + ch + "…", font_px) > limit_px:
            break
        out += ch
    return out.rstrip() + "…", True


def analyze_snippet(title, description, url=""):
    issues = []
    t_px = round(text_width_px(title, 20))
    d_px = round(text_width_px(description, 14))
    t_prev, t_cut = truncate_px(title, TITLE_LIMIT_PX, 20)
    d_prev, d_cut = truncate_px(description, DESC_LIMIT_PX, 14)
    if not title:
        issues.append(issue("error", "TITLE_EMPTY", "No title."))
    elif t_cut:
        issues.append(issue("warning", "TITLE_TRUNCATED", "Title is ~%d px; Google typically cuts near %d px." % (t_px, TITLE_LIMIT_PX)))
    elif t_px < 300:
        issues.append(issue("info", "TITLE_SHORT", "Title only uses ~%d px of ~%d px; room for a keyword or benefit." % (t_px, TITLE_LIMIT_PX)))
    if not description:
        issues.append(issue("warning", "DESC_EMPTY", "No meta description; Google will pick text from the page."))
    elif d_cut:
        issues.append(issue("warning", "DESC_TRUNCATED", "Description is ~%d px; likely truncated past ~%d px." % (d_px, DESC_LIMIT_PX)))
    elif d_px < 400:
        issues.append(issue("info", "DESC_SHORT", "Description is short (~%d px)." % d_px))
    return {
        "title_chars": len(title), "title_px": t_px, "title_preview": t_prev, "title_truncated": t_cut,
        "description_chars": len(description), "description_px": d_px, "description_preview": d_prev,
        "description_truncated": d_cut, "url": url,
        "note": "Pixel widths are estimates from Arial metrics; Google's real truncation varies.",
        "issues": sort_issues(issues),
    }


def preview_html(r):
    e = _html.escape
    return ("<!doctype html><meta charset=utf-8><title>SERP preview</title>"
            "<body style='font-family:Arial,sans-serif;max-width:640px;margin:40px auto'>"
            "<div style='color:#4d5156;font-size:14px'>%s</div>"
            "<div style='color:#1a0dab;font-size:20px;margin:2px 0'>%s</div>"
            "<div style='color:#4d5156;font-size:14px;line-height:1.58'>%s</div>"
            "<p style='color:#888;font-size:12px'>%s</p></body>") % (
        e(r["url"] or "example.com"), e(r["title_preview"]), e(r["description_preview"]), e(r["note"]))


def _args(p):
    p.add_argument("target", nargs="?", help="URL or HTML file to read title/description from")
    p.add_argument("--title", help="title text (overrides the page)")
    p.add_argument("--description", help="meta description text (overrides the page)")
    p.add_argument("--html", metavar="FILE", help="write an HTML preview file")
    add_net_args(p)


def _run(a):
    title, desc, url = a.title, a.description, a.target or ""
    if a.target and (title is None or desc is None):
        page, resp = load_page(a.target, a.ua, a.timeout)
        title = page.title if title is None else title
        desc = page.description if desc is None else desc
        url = resp.final_url if resp else a.target
    if title is None and desc is None:
        raise ToolError("give a URL/file, or --title and/or --description")
    r = analyze_snippet(title or "", desc or "", url)
    if a.html:
        with open(a.html, "w", encoding="utf-8") as f:
            f.write(preview_html(r))
        r["html_preview"] = a.html
    return r


TOOLS = [Tool("serp", "SEO / On-page", "Google snippet preview + pixel-width check for title and description", _args, _run)]
