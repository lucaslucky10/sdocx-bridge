"""Split a run's strokes into glyphs using its transcript (dynamic programming)."""
import math
import unicodedata

import numpy as np

from .scripts import FROM_UNICODE, is_script_token
from .segment import bbox

VEC = '⃗'


def tokens(text):
    """Characters of a transcript. A combining arrow stays with its letter; '_x' / '^x' (or the Unicode
    form, e.g. 'ₚ', '²') is one sub/superscript token. Spaces are dropped but marked on the next token."""
    # ẑ and z+combining hat are the same token (letters only: Hangul stays composed, ≠ stays ≠)
    text = ''.join(unicodedata.normalize('NFD', c) if c.isalpha() and not '가' <= c <= '힣' else c for c in text)
    out, space, i = [], False, 0
    while i < len(text):
        c = text[i]
        i += 1
        if c == ' ':
            space = True
            continue
        if unicodedata.combining(c) and out:
            out[-1] = (out[-1][0] + c, out[-1][1])
            continue
        if c in '_^' and i < len(text) and text[i] == '{' and '}' in text[i:]:
            j = text.index('}', i)
            out.extend((c + d, space and k == 0) for k, d in enumerate(text[i + 1:j]))
            i, space = j + 1, False
            continue
        if c in '_^' and i < len(text) and text[i] != ' ':
            c, i = c + text[i], i + 1
        c = FROM_UNICODE.get(c, c)
        out.append((c, space))
        space = False
    return out


def is_hangul(c):
    return '가' <= c[0] <= '힣'


def is_script(tok):
    """Sub/superscript token ('_p', '^2'), written small in the notes."""
    return is_script_token(tok)


def expected_width(tok):
    c = tok[0]
    if is_hangul(c):
        return 1.0
    if c in '①②③④⑤⑥⑦⑧⑨':
        return 1.1
    if c in '→↔⟶←':
        return 1.6
    if c in 'mwMWω':
        return 0.85
    if is_script(tok):
        return 0.3
    if c in 'ΣΔ':
        return 0.7
    if c in '·˙':
        return 0.12
    if c in '*×≠θ∫':
        return 0.55
    if c in 'il1|!I':
        return 0.2
    if c in 'jftr':
        return 0.42
    if c in '()[]{}':
        return 0.3
    if c in ',.:;\'`':
        return 0.12
    if c in '-=+<>≤≥≈~/':
        return 0.6
    if c.isupper():
        return 0.7
    return 0.55


def _cost_group(b, tok, u):
    w = b[2] - b[0]
    e = expected_width(tok)
    return 2.0 * math.log((w + 0.15 * u) / ((e + 0.15) * u)) ** 2


def align(strokes, text):
    """Return (glyphs, score). glyphs = list of (token, space_before, [stroke indices])."""
    toks = tokens(text)
    k = len(toks)
    order = sorted(range(len(strokes)), key=lambda i: (bbox(strokes[i])[0] + bbox(strokes[i])[2]) / 2)
    n = len(order)
    if k == 0 or n < k:
        return None, float('inf')
    B = [bbox(strokes[i]) for i in order]
    width = max(b[2] for b in B) - min(b[0] for b in B)
    # a lone straight "1" or "|" can have no width at all
    width = max(width, 0.2 * (max(b[3] for b in B) - min(b[1] for b in B)), 1e-6)
    u = width / (sum(expected_width(t) for t in toks) + 0.4 * sum(1 for t in toks if t[1]))

    def gbox(i, j):
        bs = B[i:j]
        return (min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs))

    def internal_gap(i, j):
        iv = sorted((b[0], b[2]) for b in B[i:j])
        g, hi = 0.0, iv[0][1]
        for a, c in iv[1:]:
            g = max(g, a - hi)
            hi = max(hi, c)
        return g

    for _ in range(2):
        INF = float('inf')
        f = [[INF] * (k + 1) for _ in range(n + 1)]
        bk = [[None] * (k + 1) for _ in range(n + 1)]
        f[0][0] = 0.0
        boxes = {}
        for j in range(1, n + 1):
            for m in range(1, min(k, j) + 1):
                best, arg = INF, None
                for i in range(m - 1, j):
                    if f[i][m - 1] == INF:
                        continue
                    if j - i > 40:
                        continue
                    gb = boxes.get((i, j)) or boxes.setdefault((i, j), gbox(i, j))
                    gp = 0.6 if is_hangul(toks[m - 1][0]) else 2.0
                    c = f[i][m - 1] + _cost_group(gb, toks[m - 1], u) + gp * internal_gap(i, j) / u
                    if i > 0 and bk[i][m - 1] is not None:
                        pb = boxes[(bk[i][m - 1], i)]
                        c += 4.0 * max(0.0, pb[2] - gb[0]) / u
                    if c < best:
                        best, arg = c, i
                f[j][m], bk[j][m] = best, arg
        if f[n][k] == INF:
            return None, INF
        cuts, j, m = [], n, k
        while m > 0:
            i = bk[j][m]
            cuts.append((i, j))
            j, m = i, m - 1
        cuts.reverse()
        hw = [boxes[c][2] - boxes[c][0] for c, t in zip(cuts, toks) if is_hangul(t[0])]
        if len(hw) >= 2:
            u = float(np.median(hw))
        else:
            break
    glyphs = [(t[0], t[1], [order[x] for x in range(a, b)]) for (a, b), t in zip(cuts, toks)]
    return glyphs, f[n][k] / k, u
