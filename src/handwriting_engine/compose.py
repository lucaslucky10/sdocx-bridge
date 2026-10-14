"""Write text (with $...$ LaTeX) in the user's handwriting.

Units: 1.0 = height of one Hangul syllable ("u"). Output coordinates are in u, y down, baseline-based.
"""
import json
import math
import os
import random
import re
import subprocess
import unicodedata
import xml.etree.ElementTree as ET

import numpy as np

from .align import tokens
from .derive import ACCENT, Deriver
from .hangul import JamoBank

HERE = os.path.dirname(os.path.abspath(__file__))
TEX2SVG = os.environ.get('HW_TEX2SVG', os.path.join(HERE, 'math', 'tex2svg.js'))
INK = (39, 39, 39)


class GlyphDB:
    def __init__(self, path, exclude_src=(), synth=True):
        D = json.load(open(path, encoding='utf-8'))
        self.synth = synth
        self._bank = None
        self.made = set()  # syllables built from jamo in this session
        self.derived = set()  # symbols built from other glyphs (Z from z, ∇ from Δ, ±)
        self.by = {}
        for g in D['glyphs']:
            if g['src'] in exclude_src:
                continue
            self.by.setdefault(g['ch'], []).append(g)
        # A letter or digit's height on the line was measured against its own line's baseline. On lines
        # with Hangul that baseline is the bottom of the syllables, which is what matters when the letter is
        # written next to Hangul; on lines without Hangul it is the letters' own bottom. Move each variant
        # so its group sits where the letter sits next to Hangul, and pull it halfway to the group's
        # usual height (Haines et al. 2016 keep glyphs "probably on the baseline" the same way).
        han_runs = {r['src'] for r in D.get('runs', []) if any('가' <= c <= '힣' for c in r['text'])}
        for ch, vs in self.by.items():
            if len(vs) < 3 or '가' <= ch[0] <= '힣':
                continue
            grp = {True: [g['bot'] for g in vs if g['src'] in han_runs],
                   False: [g['bot'] for g in vs if g['src'] not in han_runs]}
            med = {k: float(np.median(v)) for k, v in grp.items() if v}
            target = med[True] if len(grp[True]) >= 3 else float(np.median([g['bot'] for g in vs]))
            for i, g in enumerate(vs):
                m = med[g['src'] in han_runs]
                d = target + 0.5 * (g['bot'] - m) - g['bot']
                if abs(d) > 1e-4:
                    vs[i] = dict(g, top=g['top'] + d, bot=g['bot'] + d,
                                 strokes=[dict(st, p=[[x, y + d] for x, y in st['p']]) for st in g['strokes']])
        st = D.get('stats', {})
        self.gap = st.get('gap', 0.19)
        self.space = st.get('space', 0.49)
        self.pen = st.get('pen', 0.097)
        self.gaps = st.get('gaps', {})
        self._last = {}
        self.deriver = Deriver(self.by, self.pen)

    @property
    def bank(self):
        if self._bank is None:
            self._bank = JamoBank(self.by)
        return self._bank

    def _makeable(self, ch):
        return self.synth and len(ch) == 1 and '가' <= ch <= '힣' and self.bank.can_make(ch)

    def has(self, ch):
        return ch in self.by or self._makeable(ch) or self.deriver.has(ch)

    def sample_gap(self, prev, ch, space, rng):
        """Gap before ch, drawn from the gaps this writer left between the same kind of neighbours."""
        sp = '_' if space else ''
        q = self.gaps.get(char_class(prev) + char_class(ch) + sp) or self.gaps.get('*' + sp)
        if not q:
            return (self.space if space else self.gap) + rng.gauss(0, 0.04)
        t = rng.random() * 18 + 1  # 5%..95%: the extremes are mostly cutting errors
        i = int(t)
        v = q[i] + (q[i + 1] - q[i]) * (t - i)
        return max(v, 0.03 if not space else 0.25)

    def pick(self, ch, rng):
        """A random variant, never the same one twice in a row. Hangul syllables never seen in the notes
        are assembled from the writer's own jamo."""
        if ch not in self.by:
            if self._makeable(ch):
                self.made.add(ch)
                return self.bank.make(ch, rng)
            g = self.deriver.make(ch, rng)
            if g is not None and g.get('derived'):
                self.derived.add(ch)
            return g
        vs = self.by[ch]
        if len(vs) == 1:
            return vs[0]
        last = self._last.get(ch)
        choices = [v for v in vs if v is not last]
        g = rng.choice(choices)
        self._last[ch] = g
        return g


def char_class(c):
    """Kind of character, for spacing: Hangul, letter, digit, punctuation, open/close bracket, other."""
    if not c:
        return 'O'
    c = c.lstrip('_^')[:1] or c[:1]
    if '가' <= c <= '힣':
        return 'H'
    if c.isdigit():
        return 'D'
    if c.isalpha():
        return 'L'
    if c in '.,:;\'"!?·':
        return 'P'
    if c in '([{⟨':
        return 'B'
    if c in ')]}⟩':
        return 'C'
    return 'O'


def _jitter(rng, amount):
    """Small random scale, rotation and shift so repeated letters never look stamped."""
    # each variant already sits where the writer put it; the jitter only keeps repeats from looking stamped
    return {'s': 1 + rng.gauss(0, 0.02 * amount), 'rot': rng.gauss(0, 1.0 * amount) * math.pi / 180,
            'dy': rng.gauss(0, 0.008 * amount), 'dx': 0.0}


def place(g, x, y, scale=1.0, jit=None, color=None, pen=None):
    """Return strokes of glyph g with its left edge at x and baseline at y."""
    j = jit or {'s': 1, 'rot': 0, 'dy': 0, 'dx': 0}
    s = scale * j['s']
    cx, cy = g['adv'] / 2, (g['top'] + g['bot']) / 2
    c, sn = math.cos(j['rot']), math.sin(j['rot'])
    out = []
    for st in g['strokes']:
        p = np.array(st['p'], float)
        px, py = p[:, 0] - cx, p[:, 1] - cy
        rx, ry = px * c - py * sn, px * sn + py * c
        out.append({'x': x + (rx + cx) * s + j['dx'] * scale, 'y': y + (ry + cy) * s + j['dy'] * scale,
                    # one pen: small (sub/superscript) glyphs keep nearly the same line width
                    'w': (pen or st['w']) * min(1.0, max(scale, 0.85)), 'c': tuple(color or st['c'])})
    return out, g['adv'] * s


# ---------------------------------------------------------------- math

def _char_from_codepoint(cp):
    ch = chr(cp)
    try:
        name = unicodedata.name(ch)
    except ValueError:
        return ch
    m = re.match(r'MATHEMATICAL (?:ITALIC|BOLD ITALIC|BOLD|SANS-SERIF ITALIC) (SMALL|CAPITAL) (.+)$', name)
    if m:
        case, letter = m.groups()
        if len(letter) == 1:
            return letter.lower() if case == 'SMALL' else letter
        try:
            g = unicodedata.lookup(f'GREEK {case.replace("SMALL", "SMALL").replace("CAPITAL", "CAPITAL")} LETTER {letter}')
            return g
        except KeyError:
            return ch
    m = re.match(r'MATHEMATICAL (?:ITALIC|BOLD ITALIC|BOLD) (\w+) SYMBOL$', name)
    if m:
        letter = {'EPSILON': 'LUNATE EPSILON'}.get(m.group(1), m.group(1))
        try:
            return unicodedata.lookup(f'GREEK {letter} SYMBOL')
        except KeyError:
            return ch
    if name.startswith('MATHEMATICAL') and name.endswith('PARTIAL DIFFERENTIAL'):
        return '∂'
    if name.startswith('MATHEMATICAL') and name.endswith('NABLA'):
        return '∇'
    if ch == 'ℎ':
        return 'h'
    if ch == '−':
        return '-'
    if ch == '′':
        return "'"
    return ch


# Last resort when neither the symbol nor a glyph derived from it exists.
MATH_ALIASES = {'ϵ': 'e', 'ε': 'e', '∗': '*', '⋅': '·', '∑': 'Σ', 'ˆ': '^', '˜': '~', 'ˉ': '¯'}
# set to the height TeX gives them (tall parentheses around a fraction, display integrals)
STRETCH = {c: 0.92 for c in '()[]{}|‖⟨⟩∫∮∬∭'}
STRETCH.update({c: 0.7 for c in 'Σ∑∏Π'})  # display sums: big, but not as big as TeX sets them


def _accent_bases(items):
    """TeX sets an accent (vector arrow, hat, dot) as its own glyph above the letter. Pair each accent with
    the glyph under it: returns {accent index: base index}."""
    out = {}
    for i, it in enumerate(items):
        if it[0] != 'glyph' or it[1] not in ACCENT:
            continue
        bb = it[5]
        cx = (bb[0] + bb[2]) / 2
        best = None
        for j, jt in enumerate(items):
            if j == i or jt[0] != 'glyph' or jt[1] in ACCENT:
                continue
            b = jt[5]
            if b[0] - 50 <= cx <= b[2] + 50 and b[3] <= bb[1] + 150:
                d = bb[1] - b[3]
                if best is None or d < best[0]:
                    best = (d, j)
        if best:
            out[i] = best[1]
    return out


def _script_parent(items, i):
    """The full-size glyph a sub/superscript belongs to: the last glyph before it, set larger and ending
    where the script starts. Limits under Σ or lim sit inside their operator's width and get none."""
    it = items[i]
    if it is None or it[0] != 'glyph' or it[4] >= 0.8:
        return None
    for j in range(i - 1, -1, -1):
        p = items[j]
        if p is None or p[0] != 'glyph':
            continue
        if p[4] > it[4] * 1.15 and p[5][2] <= it[5][0] + 60:
            return p
        if p[4] > it[4] * 1.15:
            return None
    return None


def sqrt_parts(g):
    """Split a handwritten radical into its check mark (up to the top-left corner) and the bar after it.
    Returns two point arrays; the bar starts at the corner."""
    pts = np.concatenate([np.asarray(st['p'], float) for st in sorted(g['strokes'], key=lambda st: np.asarray(st['p'])[:, 0].min())])
    top, h = pts[:, 1].min(), np.ptp(pts[:, 1])
    near = np.where(pts[:, 1] <= top + 0.15 * h)[0]
    ci = int(near[np.argmin(pts[near, 0])]) if len(near) else int(np.argmin(pts[:, 1]))
    hook, bar = pts[:ci + 1], pts[ci:]
    if len(bar) < 2:
        bar = np.array([pts[ci], pts[ci] + [0.3, 0]])
    return hook, bar


def place_box(g, cx, cy, sx, sy, color, pen):
    """Strokes of g scaled by (sx, sy) about its centre, centred at (cx, cy)."""
    gx, gy = g['adv'] / 2, (g['top'] + g['bot']) / 2
    out = []
    for st in g['strokes']:
        p = np.array(st['p'], float)
        out.append({'x': cx + (p[:, 0] - gx) * sx, 'y': cy + (p[:, 1] - gy) * sy, 'w': pen or st['w'],
                    'c': tuple(color or st['c'])})
    return out

_NUM = re.compile(r'-?\d*\.?\d+(?:e-?\d+)?')


def _path_bbox(d):
    nums = [float(v) for v in _NUM.findall(d)]
    toks = re.findall(r'[A-Za-z]|-?\d*\.?\d+', d)
    xs, ys, cmd, buf = [], [], None, []
    for t in toks:
        if t.isalpha():
            cmd, buf = t, []
            continue
        buf.append(float(t))
        if cmd in 'MLTQCSmltqcs' and len(buf) == 2:
            xs.append(buf[0]); ys.append(buf[1]); buf = []
        elif cmd in 'Hh':
            xs.append(buf[0]); buf = []
        elif cmd in 'Vv':
            ys.append(buf[0]); buf = []
    if not xs or not ys:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def _parse_transform(t):
    M = np.eye(3)
    for name, args in re.findall(r'(\w+)\(([^)]*)\)', t or ''):
        a = [float(v) for v in re.split(r'[ ,]+', args.strip()) if v]
        if name == 'translate':
            T = np.array([[1, 0, a[0]], [0, 1, a[1] if len(a) > 1 else 0], [0, 0, 1]])
        elif name == 'scale':
            T = np.array([[a[0], 0, 0], [0, a[1] if len(a) > 1 else a[0], 0], [0, 0, 1]])
        elif name == 'matrix':
            T = np.array([[a[0], a[2], a[4]], [a[1], a[3], a[5]], [0, 0, 1]])
        else:
            continue
        M = M @ T
    return M


def tex_layout(tex, display=False):
    """Typeset with MathJax. Returns list of items in em/1000 units (y up):
    ('glyph', char, origin_x, origin_y, scale, bbox) and ('rule', x0, y0, x1, y1)."""
    try:
        out = subprocess.run(['node', TEX2SVG, tex, 'display' if display else 'inline'],
                             capture_output=True, text=True, encoding='utf-8', timeout=30, check=True).stdout
    except FileNotFoundError as exc:
        raise ValueError('LaTeX needs Node.js and the optional MathJax installation; see README.') from exc
    except subprocess.TimeoutExpired as exc:
        raise ValueError('LaTeX layout exceeded 30 seconds; simplify the formula.') from exc
    except subprocess.CalledProcessError as exc:
        raise ValueError('LaTeX renderer failed. Check the optional MathJax installation and formula.') from exc
    try:
        root = ET.fromstring(out)
    except ET.ParseError as exc:
        raise ValueError('LaTeX renderer returned invalid SVG') from exc
    if any(el.get('data-mml-node') == 'merror' for el in root.iter()):
        raise ValueError('Invalid LaTeX formula; correct it before generating handwriting.')
    if any(el.tag.rsplit('}', 1)[-1] in ('text', 'image', 'foreignObject') for el in root.iter()):
        raise ValueError('LaTeX contains unsupported text or images. Put Korean text outside $...$; no formula content was omitted.')
    ns = '{http://www.w3.org/2000/svg}'
    items = []

    def walk(el, M):
        M = M @ _parse_transform(el.get('transform'))
        tag = el.tag.replace(ns, '')
        if tag == 'path' and el.get('data-c'):
            bb = _path_bbox(el.get('d', ''))
            if bb:
                cp = int(el.get('data-c'), 16)
                o = M @ np.array([0, 0, 1])
                p0 = M @ np.array([bb[0], bb[1], 1])
                p1 = M @ np.array([bb[2], bb[3], 1])
                items.append(('glyph', _char_from_codepoint(cp), o[0], o[1], abs(M[0, 0]),
                              (min(p0[0], p1[0]), min(p0[1], p1[1]), max(p0[0], p1[0]), max(p0[1], p1[1]))))
        elif tag == 'rect':
            x, y = float(el.get('x', 0)), float(el.get('y', 0))
            w, h = float(el.get('width', 0)), float(el.get('height', 0))
            p0 = M @ np.array([x, y, 1]); p1 = M @ np.array([x + w, y + h, 1])
            items.append(('rule', min(p0[0], p1[0]), min(p0[1], p1[1]), max(p0[0], p1[0]), max(p0[1], p1[1])))
        for c in el:
            walk(c, M)

    walk(root, np.eye(3))
    # the top group flips y (scale(1,-1)); convert so that y is up = positive
    fixed = []
    for it in items:
        if it[0] == 'glyph':
            _, ch, ox, oy, s, bb = it
            fixed.append(('glyph', ch, ox, -oy, s, (bb[0], -bb[3], bb[2], -bb[1])))
        else:
            _, x0, y0, x1, y1 = it
            fixed.append(('rule', x0, -y1, x1, -y0))
    return fixed


def pen_line(x0, y0, x1, y1, w, rng, color=INK):
    color = color or INK
    """A hand-drawn straight line: slight bow and uneven ends."""
    n = 12
    t = np.linspace(0, 1, n)
    bow = rng.gauss(0, 0.012) * math.hypot(x1 - x0, y1 - y0)
    xs = x0 + (x1 - x0) * t
    ys = y0 + (y1 - y0) * t + bow * np.sin(np.pi * t)
    return {'x': xs, 'y': ys, 'w': w, 'c': color}




def _close_gaps(spans, max_gap, rows=None, min_gap=0.0, elastic=()):
    """TeX puts wide spaces around = + etc.; handwriting does not. Given each item's ink span (x0, x1),
    return a left shift per item that shrinks every horizontal gap to at most max_gap, keeping stacked
    items (fractions) together. With rows (each item's y0, y1), items side by side on the same row whose
    handwritten ink would overlap are pushed apart to at least min_gap. Items in elastic (root overlines)
    stretch to what they cover, so they never hold the items after them apart."""
    order = sorted(range(len(spans)), key=lambda i: spans[i][0])
    shift, reach, total, placed = [0.0] * len(spans), None, 0.0, []
    for i in order:
        a, b = spans[i]
        if reach is not None and a - total - reach > max_gap:
            total += (a - total - reach) - max_gap
        if rows is not None:
            y0, y1 = rows[i]
            for pb, q0, q1 in placed:
                if min(y1, q1) - max(y0, q0) > 0.3 * min(y1 - y0, q1 - q0) and a - total < pb + min_gap:
                    total -= pb + min_gap - (a - total)
            placed.append((b - total, y0, y1))
        shift[i] = total
        if i in elastic:
            continue
        reach = b - total if reach is None else max(reach, b - total)
    return shift


def math_strokes(db, tex, rng, x, base, jitter=1.0, missing=None, em_to_u=None, squeeze=0.8, color=INK,
                 synth_color=None):
    """Lay out TeX at (x, baseline). Returns (strokes, width).
    Fractions are set full size (\\displaystyle), as they are written by hand."""
    items = tex_layout('\\displaystyle ' + tex)
    # 1u (Hangul height) ~ height of a digit (0.666 em in TeX fonts)
    k = em_to_u or (1.0 / 666.0)
    kx = k * squeeze  # handwriting is set tighter than TeX
    # choose the handwritten glyph for every item first, so spacing can follow the real ink widths
    plan, spans, rows = [], [], []
    items = list(items)
    accents = _accent_bases(items)
    for ai, bi in accents.items():
        it = items[bi]
        if it[1] + ACCENT[items[ai][1]] in db.by:
            # the writer's own letter-with-accent glyph (E⃗, x̂) stands in for the letter and its accent
            items[bi] = ('glyph', it[1] + ACCENT[items[ai][1]]) + tuple(it[2:])
            items[ai] = None
    for i, it in enumerate(items):
        if it is None:
            continue
        if it[0] == 'rule':
            plan.append(it)
            spans.append((it[1], it[3]))
            rows.append((it[2], it[4]))
            continue
        _, ch, ox, oy, s, bb = it
        if not db.has(ch) and db.has(MATH_ALIASES.get(ch, ch)):
            ch = MATH_ALIASES[ch]
        p = _script_parent(items, i)
        if p is not None:
            rise = oy - p[3]
            if -450 < rise < -40 and '_' + ch in db.by:
                # the user's own small subscript glyph; it already sits below its letter's baseline
                ch, s, oy = '_' + ch, p[4], p[3]
            elif 120 < rise < 700 and '^' + ch in db.by:
                ch, s, oy = '^' + ch, p[4], p[3]
        g = db.pick(ch, rng) if ch != '√' and db.has(ch) else None
        mode, sx, sy = 'base', s, s
        if g is not None:
            gh = max(g['bot'] - g['top'], 1e-3)
            bh = (bb[3] - bb[1]) * k
            if i in accents:
                # an accent with no glyph of its own pairing: fit it into the box TeX gives it
                mode = 'box'
                sx = sy = min((bb[2] - bb[0]) * kx / max(g['adv'], 1e-3), 1.5 * max(bh, 0.15) / gh)
            elif ch in STRETCH and STRETCH[ch] * bh > 1.25 * gh * s:
                # tall brackets, display sums and integrals: about as tall as TeX sets them, only a little wider
                mode, sy = 'box', STRETCH[ch] * bh / gh
                sx = s * (sy / s) ** (0.35 if ch in '()[]{}|‖⟨⟩∫∮∬∭' else 0.8)
            else:
                # a handwritten arrow can be far wider than TeX's; keep it close to the room TeX gives
                tw = (bb[2] - bb[0]) * kx
                if g['adv'] * s > max(1.4 * tw, tw + 0.3):
                    sx = sy = s = max(1.2 * tw, tw + 0.2) / g['adv']
        c = (bb[0] + bb[2]) / 2
        half = g['adv'] * sx / kx / 2 if g else (bb[2] - bb[0]) / 2
        plan.append(('glyph', ch, oy, s, bb, g, mode, sx, sy))
        if ch == '√' and db.by.get('√'):
            # the handwritten radical is a narrow check mark at the right end of TeX's wide surd
            spans.append((bb[2] - 0.3 * (bb[3] - bb[1]) * k / kx, bb[2]))
        else:
            spans.append((c - half, c + half))
        rows.append((bb[1], bb[3]))
    # a root's overline stretches over what is under it; fraction bars keep their place (they group the
    # numerator and denominator)
    roots = [it[4] for it in plan if it[0] == 'glyph' and it[1] == '√']
    elastic = {i for i, it in enumerate(plan) if it[0] == 'rule'
               and any(abs(it[1] - bb[2]) < 80 and abs(it[4] - bb[3]) < 80 for bb in roots)}
    shift = _close_gaps(spans, max_gap=db.gap * 1.2 / kx, rows=rows, min_gap=0.12 / kx, elastic=elastic)
    strokes = []
    right = x
    rules, inks, pending_root = [], [], {}
    for it, d in zip(plan, shift):
        if it[0] == 'rule':
            rules.append((it, d))
            continue
        _, ch, oy, s, bb, g, mode, sx, sy = it
        gx0, gx1 = x + (bb[0] - d) * kx, x + (bb[2] - d) * kx
        if ch == '√' and db.by.get('√'):
            # the writer's own radical: its check-mark part fitted to TeX's surd, its bar run out over
            # whatever is under the root (joined to the overline rule below)
            right = max(right, gx1)
            y_top, y_bot = base - bb[3] * k, base - bb[1] * k
            hook, bar = sqrt_parts(rng.choice(db.by['√']))
            sc = (y_bot - y_top) / max(np.ptp(hook[:, 1]), 1e-6)
            hx = gx1 + (hook[:, 0] - hook[-1, 0]) * sc * 0.9
            hy = y_top + (hook[:, 1] - hook[:, 1].min()) * sc
            rule = next((r for r in plan if r[0] == 'rule' and abs(r[1] - bb[2]) < 80 and abs(r[4] - bb[3]) < 80), None)
            pending_root[id(rule) if rule is not None else None] = (hx, hy, bar, sc)
            if rule is None:  # no overline: keep the bar as written
                bx = hx[-1] + (bar[:, 0] - bar[0, 0]) * sc
                strokes.append({'x': np.concatenate([hx, bx[1:]]), 'y': np.concatenate([hy, hy[-1] + (bar[1:, 1] - bar[0, 1]) * sc]),
                                'w': db.pen, 'c': color})
            continue
        if ch == '√':  # radical sign: drawn as a pen check mark
            right = max(right, gx1)
            y_top, y_bot = base - bb[3] * k, base - bb[1] * k
            xs = np.array([gx0, gx0 + 0.25 * (gx1 - gx0), gx0 + 0.55 * (gx1 - gx0), gx1])
            ys = np.array([y_bot - 0.45 * (y_bot - y_top), y_bot - 0.55 * (y_bot - y_top), y_bot, y_top])
            strokes.append({'x': xs, 'y': ys, 'w': db.pen, 'c': color})
            continue
        if g is None:
            if missing is not None:
                missing.add(ch)
            right = max(right, gx1)
            y0m, y1m = base - bb[3] * k, base - bb[1] * k
            strokes.append({'x': np.array([gx0, gx1, gx1, gx0, gx0]), 'y': np.array([y1m, y1m, y0m, y0m, y1m]),
                            'w': 0.02, 'c': (235, 120, 120), 'missing': ch})
            continue
        cxm = (gx0 + gx1) / 2
        gc = synth_color if synth_color and g.get('derived') else color
        if mode == 'box':
            strokes.extend(place_box(g, cxm, base - (bb[1] + bb[3]) / 2 * k, sx, sy, gc, db.pen))
            w = g['adv'] * sx
            right = max(right, cxm + w / 2)
            inks.append((bb[0], bb[2], cxm - w / 2, cxm + w / 2))
            continue
        w = g['adv'] * s
        st, _ = place(g, cxm - w / 2, base - oy * k, s, _jitter(rng, jitter), gc, db.pen)
        strokes.extend(st)
        right = max(right, cxm + w / 2)
        inks.append((bb[0], bb[2], cxm - w / 2, cxm + w / 2))
    for rule, d in rules:
        _, x0, y0, x1, y1 = rule
        # a fraction bar spans what is written above and below it
        a, b = x + (x0 - d) * kx, x + (x1 - d) * kx
        cov = [(ia, ib) for t0, t1, ia, ib in inks if t0 >= x0 - 1 and t1 <= x1 + 1]
        if cov:
            a = min(a, min(c[0] for c in cov) - 0.08)
            b = max(b, max(c[1] for c in cov) + 0.08)
        if id(rule) in pending_root:
            hx, hy, bar, sc = pending_root.pop(id(rule))
            if cov:  # exactly over what is under the root (the TeX rule's end moved less than its contents)
                b = max(c[1] for c in cov) + 0.1
            # the written bar's own small waves, stretched to reach the end of the root
            t = (bar[:, 0] - bar[0, 0]) / max(bar[-1, 0] - bar[0, 0], 1e-6)
            bx = hx[-1] + t * (b - hx[-1])
            by = hy[-1] + (bar[:, 1] - bar[0, 1]) * min(sc, 1.0)
            strokes.append({'x': np.concatenate([hx, bx[1:]]), 'y': np.concatenate([hy, by[1:]]), 'w': db.pen,
                            'c': color})
            right = max(right, b)
            continue
        yy = base - (y0 + y1) / 2 * k
        strokes.append(pen_line(a, yy, b, yy + rng.gauss(0, 0.01), db.pen, rng, color))
        right = max(right, b)
    # start the ink at x (a handwritten radical is narrower than TeX's surd, so TeX's left edge is empty)
    real = [st for st in strokes if 'missing' not in st] or strokes
    if real:
        lead = min(float(np.min(st['x'])) for st in real) - x
        if lead > 0:
            for st in strokes:
                st['x'] = np.asarray(st['x']) - lead
            right -= lead
    return strokes, right - x


# ---------------------------------------------------------------- text

SEG = re.compile(r'\$([^$]+)\$')


def compose(db, text, width=40.0, line_gap=2.1, seed=None, jitter=1.0, color=INK, synth_color=None, log=None):
    """Lay out text with inline $math$. Returns (strokes, missing chars, (W, H)).
    log: optional list that receives one box per text glyph (for tools/compare_real.py)."""
    rng = random.Random(seed)
    strokes, missing = [], set()
    x, base, maxx = 0.0, 1.0, 0.0
    starts = [0]  # index of the first stroke of every visual line
    for li, line in enumerate(text.split('\n')):
        if li:
            x, base = 0.0, base + line_gap + rng.gauss(0, 0.03)
            starts.append(len(strokes))
        drift = rng.gauss(0, 0.005)  # lines run slightly up or down
        pos = 0
        parts = []
        for m in SEG.finditer(line):
            parts.append(('text', line[pos:m.start()]))
            parts.append(('math', m.group(1)))
            pos = m.end()
        parts.append(('text', line[pos:]))
        prev = None  # last text glyph on this line, for the gap before the next one
        for kind, val in parts:
            if kind == 'math':
                prev = None
                if x > 0:
                    x += db.gap
                st, w = math_strokes(db, val, rng, x, base + drift * x, jitter, missing, color=color,
                                     synth_color=synth_color)
                strokes.extend(st)
                x += w + db.gap
                continue
            for ch, sp in tokens(val):
                if prev is not None:
                    x += db.sample_gap(prev, ch, sp, rng)
                elif sp and x > 0:
                    x += db.space - db.gap + rng.gauss(0, 0.04)
                if x > width:
                    x, base = 0.0, base + line_gap
                    starts.append(len(strokes))
                if not db.has(ch):
                    missing.add(ch)
                    bx = np.array([x, x + 0.6, x + 0.6, x, x]); by = base - np.array([0, 0, 1, 1, 0])
                    strokes.append({'x': bx, 'y': by, 'w': 0.02, 'c': (235, 120, 120), 'missing': ch})
                    x += 0.6
                    prev = ch
                    continue
                g = db.pick(ch, rng)
                c = synth_color if synth_color and (g.get('synth') or g.get('derived')) else color
                st, adv = place(g, x, base + drift * x, 1.0, _jitter(rng, jitter), c, db.pen)
                strokes.extend(st)
                if log is not None:
                    xs = np.concatenate([q['x'] for q in st]); ys = np.concatenate([q['y'] for q in st])
                    log.append({'ch': ch, 'space': sp, 'x0': float(xs.min()), 'x1': float(xs.max()),
                                'top': float(ys.min()), 'bot': float(ys.max()), 'line': len(starts) - 1,
                                'synth': bool(g.get('synth'))})
                x += adv
                prev = ch
            if val.endswith(' ') and prev is not None:
                x += db.space - db.gap
        maxx = max(maxx, x)
    # lines with tall math (fractions, sums) push the next line down instead of overlapping it
    starts.append(len(strokes))
    prev_bot, total = None, 0.0
    for line_index, (a, b) in enumerate(zip(starts, starts[1:])):
        line = [st for st in strokes[a:b] if 'missing' not in st] or strokes[a:b]
        if not line:
            continue
        for st in strokes[a:b]:
            st['y'] = np.asarray(st['y']) + total
            st['line'] = line_index
        top = min(float(np.min(st['y'])) for st in line)
        if prev_bot is not None and top < prev_bot + 0.35:
            d = prev_bot + 0.35 - top
            total += d
            for st in strokes[a:b]:
                st['y'] = st['y'] + d
        prev_bot = max(float(np.max(st['y'])) for st in line)
    return strokes, missing, (maxx, base + total + 0.6)


# ---------------------------------------------------------------- output

def to_svg(strokes, scale=40.0, pad=0.6, width_factor=1.0):
    xs = np.concatenate([s['x'] for s in strokes]); ys = np.concatenate([s['y'] for s in strokes])
    x0, y0, x1, y1 = xs.min() - pad, ys.min() - pad, xs.max() + pad, ys.max() + pad
    W, H = (x1 - x0) * scale, (y1 - y0) * scale
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W:.0f}" height="{H:.0f}" viewBox="0 0 {W:.1f} {H:.1f}">',
             '<rect width="100%" height="100%" fill="white"/>']
    for s in strokes:
        pts = ' '.join(f'{(a - x0) * scale:.2f},{(b - y0) * scale:.2f}' for a, b in zip(s['x'], s['y']))
        if len(s['x']) == 1:
            pts += f' {(s["x"][0] - x0) * scale + 0.01:.2f},{(s["y"][0] - y0) * scale:.2f}'
        c = 'rgb(%d,%d,%d)' % tuple(s['c'])
        parts.append(f'<polyline points="{pts}" fill="none" stroke="{c}" stroke-width="{s["w"] * scale * width_factor:.2f}" '
                     'stroke-linecap="round" stroke-linejoin="round"/>')
    parts.append('</svg>')
    return '\n'.join(parts)


def to_png(strokes, path, scale=40.0, pad=0.6, width_factor=1.0, supersample=3):
    from PIL import Image, ImageDraw
    xs = np.concatenate([s['x'] for s in strokes]); ys = np.concatenate([s['y'] for s in strokes])
    x0, y0, x1, y1 = xs.min() - pad, ys.min() - pad, xs.max() + pad, ys.max() + pad
    k = scale * supersample
    im = Image.new('RGB', (int((x1 - x0) * k) + 1, int((y1 - y0) * k) + 1), 'white')
    dr = ImageDraw.Draw(im)
    for s in strokes:
        pts = [((a - x0) * k, (b - y0) * k) for a, b in zip(s['x'], s['y'])]
        w = max(1, int(round(s['w'] * k * width_factor)))
        if len(pts) > 1:
            dr.line(pts, fill=tuple(s['c']), width=w, joint='curve')
        r = w / 2
        for p in (pts[0], pts[-1]):
            dr.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r], fill=tuple(s['c']))
    im = im.resize((im.width // supersample, im.height // supersample), Image.LANCZOS)
    im.save(path)
    return im
