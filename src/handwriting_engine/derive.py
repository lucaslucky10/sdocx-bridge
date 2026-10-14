"""Characters the notes never show, built from the writer's own strokes.

Three kinds of recipe, tried in order until one has all its parts in the glyph DB:
- the same letter in the other case, when both cases share a shape (Z from z, o from O);
- a mirrored, flipped or turned glyph (∇ from Δ, ∀ from A, ← from →, ↑ from →);
- a few glyphs put together (± from + and -, ÷ from - and two dots, ! from | and .).
A handful of plain marks nobody writes in these notes ({ } ~ _) are drawn as single pen lines.
Every result carries derived=True (pen=True for drawn marks) so callers can list them.
"""
import math

import numpy as np

# letters whose capital is the small letter enlarged
SAME_SHAPE = 'cosuvwxz'

# one source glyph under a transform: (kind, source chars in order of preference, argument)
TRANSFORM = {
    '∇': ('flip_y', 'Δ'), '∀': ('flip_y', 'A'), '∃': ('flip_x', 'E'), 'Γ': ('flip_y', 'L'),
    'Λ': ('flip_y', 'V'), '←': ('flip_x', '→'), '⇐': ('flip_x', '⇒'), '>': ('flip_x', '<'), '<': ('flip_x', '>'),
    '≥': ('flip_x', '≤'), '≤': ('flip_x', '≥'), ')': ('flip_x', '('), '(': ('flip_x', ')'), ']': ('flip_x', '['),
    '[': ('flip_x', ']'), '}': ('flip_x', '{'), '{': ('flip_x', '}'), '↑': ('rot', '→', -90), '↓': ('rot', '→', 90),
    'Ψ': ('fit_cap', 'ψ'),
}

# the same symbol under another code point: the writer's own glyph, not a built one
SAME = set('∑∏′−∗⋅∙∣∶–—⟶⟹⟺ϕϑ')
# ... or a letter the writer forms the same way
ALIAS = {
    '∑': 'Σ', '∏': 'Π', 'ο': 'o', 'Ο': 'O', 'κ': 'k', 'ν': 'v', 'υ': 'u', 'χ': 'x', '′': "'", '−': '-',
    '∗': '*', '⋅': '·', '∙': '·', 'ϕ': 'φ', 'ϑ': 'θ', 'ϵ': 'ε', '∈': 'ε', 'Α': 'A', 'Β': 'B', 'Ε': 'E',
    'Ζ': 'Z', 'Η': 'H', 'Ι': 'I', 'Κ': 'K', 'Μ': 'M', 'Ν': 'N', 'Ρ': 'P', 'Τ': 'T', 'Υ': 'Y', 'Χ': 'X',
    '∣': '|', 'ℓ': 'l', '∶': ':', '–': '-', '—': '-', '⟶': '→', '⟹': '⇒', '⟺': '⇔', '∖': '\\',
}

# glyphs put together: [(source chars in order of preference, centre x, centre y, size)]
# size is a scale factor, ('w', width) or ('h', height); y is down, the baseline is 0, 1.0 = Hangul height
COMBINE = {
    '±': [('+', .22, -.6, .8), ('-', .22, -.12, ('w', .42))],
    '∓': [('-', .22, -.82, ('w', .42)), ('+', .22, -.34, .8)],
    '÷': [('-', .22, -.46, ('w', .45)), ('·.', .22, -.72, 1), ('·.', .22, -.2, 1)],
    '!': [('|l1', 0, -.6, ('h', .75)), ('.·', 0, -.03, 1)],
    ';': [('.·', 0, -.45, 1), (',', 0, -.06, 1)],
    '″': [("'", 0, -.8, 1), ("'", .13, -.8, 1)],
    '%': [('o0', .1, -.76, ('h', .3)), ('/', .32, -.47, ('h', .95)), ('o0', .55, -.18, ('h', .3))],
    'Θ': [('O0', .3, -.47, ('h', .92)), ('-', .3, -.47, ('w', .38))],
    'Φ': [('o0O', .3, -.47, ('h', .62)), ('|l1', .3, -.47, ('h', 1.1))],
    'Π': [('-', .32, -.92, ('w', .7)), ('|l1', .1, -.46, ('h', .9)), ('|l1', .54, -.46, ('h', .9))],
    '≡': [('=', .23, -.43, ('w', .45)), ('-', .23, -.7, ('w', .45))],
    '⇒': [('=', .32, -.43, ('w', .62)), ('>', .64, -.43, ('h', .5))],
    '∴': [('·.', .2, -.62, 1), ('·.', 0, -.12, 1), ('·.', .4, -.12, 1)],
    '∵': [('·.', 0, -.62, 1), ('·.', .4, -.62, 1), ('·.', .2, -.12, 1)],
    '…': [('.·', 0, -.03, 1), ('.·', .22, -.03, 1), ('.·', .44, -.03, 1)],
    '⋯': [('·.', 0, -.46, 1), ('·.', .22, -.46, 1), ('·.', .44, -.46, 1)],
    '∉': [('ε∈', .25, -.4, ('h', .6)), ('/', .25, -.4, ('h', .9))],
    '≈': [('~', .23, -.56, ('w', .45)), ('~', .23, -.3, ('w', .45))],
    '≪': [('<', 0, -.45, 1), ('<', .25, -.45, 1)],
    'Ξ': [('-', .3, -.92, ('w', .6)), ('-', .3, -.47, ('w', .45)), ('-', .3, -.02, ('w', .6))],
    '≫': [('>', 0, -.45, 1), ('>', .25, -.45, 1)],
}

# accents MathJax sets as separate glyphs; the writer's own letter+accent glyph is used when it exists
ACCENT = {'⃗': '⃗', '^': '̂', 'ˆ': '̂', '˙': '̇', '¯': '̄', 'ˉ': '̄',
          '~': '̃', '˜': '̃'}
ACCENT_FROM = {'⃗': [('→', .3, -.3, ('w', .55))], '˙': [('·.', 0, -.1, 1)], '¯': [('-', .2, -.1, ('w', .45))]}


def _strokes_box(strokes):
    p = np.concatenate([np.asarray(s['p'], float) for s in strokes])
    return p[:, 0].min(), p[:, 1].min(), p[:, 0].max(), p[:, 1].max()


def _record(ch, strokes, **flags):
    """Shift strokes so the ink starts at x=0 and wrap them as a glyph record."""
    x0, y0, x1, y1 = _strokes_box(strokes)
    out = [dict(s, p=[[x - x0, y] for x, y in s['p']]) for s in strokes]
    return dict({'ch': ch, 'src': 'derived', 'adv': float(x1 - x0), 'top': float(y0), 'bot': float(y1),
                 'strokes': out, 'derived': True}, **flags)


def _map(g, f):
    return [dict(s, p=[list(f(x, y)) for x, y in s['p']]) for s in g['strokes']]


def _scaled(g, size):
    w, h = max(g['adv'], 1e-3), max(g['bot'] - g['top'], 1e-3)
    if isinstance(size, tuple):
        return size[1] / (w if size[0] == 'w' else h)
    return float(size)


def pen_mark(ch, pen=0.095):
    """Single pen strokes for marks that are not in the notes."""
    t = np.linspace(0, 1, 24)
    if ch == '~':
        pts = np.c_[t * .5, -.45 - .07 * np.sin(t * 2 * np.pi)]
    elif ch == '_':
        pts = np.c_[t * .5, np.full_like(t, .05)]
    elif ch in '{}':
        # brace: two arcs meeting in a small point at mid-height
        y = -1.0 + t * 1.15
        d = np.abs(y + .43) / .58
        x = .18 * (1 - np.exp(-6 * d)) + .06 * d
        if ch == '}':
            x = .3 - x
        pts = np.c_[x, y]
    elif ch == '^':
        pts = np.array([[0, -.65], [.15, -.9], [.3, -.65]])
    elif ch == '√':
        pts = np.array([[0, -.45], [.1, -.52], [.25, 0], [.45, -1.0], [.85, -1.0]])
    else:
        return None
    return _record(ch, [{'p': pts.tolist(), 'w': pen, 'c': [39, 39, 39]}], pen=True)


class Deriver:
    """has(ch) / make(ch, rng) on top of a dict {char: [glyph records]} of real glyphs."""

    def __init__(self, by, pen=0.095):
        self.by = by
        self.pen = pen
        ups = [g['top'] for c in 'ABDEFHKLMNPRTUVWXZ' for g in by.get(c, [])]
        lows = [g['top'] for c in 'acemnorsuvwxz' for g in by.get(c, [])]
        self.cap = -float(np.median(ups)) if ups else 0.98
        self.low = -float(np.median(lows)) if lows else 0.55

    def _first(self, chars):
        for c in chars:
            if self.by.get(c):
                return c
        return None

    def recipe(self, ch):
        if ch in ALIAS and self.by.get(ALIAS[ch]):
            return ('alias', ALIAS[ch])
        low = ch.lower()
        if low in SAME_SHAPE:
            other = ch.upper() if ch == low else low
            if self.by.get(other):
                return ('case', other)
        if ch in TRANSFORM:
            kind, src, *arg = TRANSFORM[ch]
            if self.by.get(src):
                return (kind, src, *arg)
        parts = COMBINE.get(ch) or ACCENT_FROM.get(ch)
        if parts and all(self._first(p[0]) for p in parts):
            return ('combine', parts)
        if ch in '{}~_^√':
            return ('pen',)
        if ch == 'j' and self.by.get('J') and (self.by.get('·') or self.by.get('.')):
            return ('j',)
        return None

    def has(self, ch):
        return self.recipe(ch) is not None or self._token(ch) is not None

    def _get(self, ch, rng):
        """A real glyph if the notes have one, else a derived one."""
        if self.by.get(ch):
            return rng.choice(self.by[ch])
        return self.make(ch, rng)

    def _token(self, tok):
        """Multi-character tokens: '_x' / '^2' scripts and a letter with a combining accent."""
        if len(tok) < 2:
            return None
        if tok[0] in '_^' and len(tok) == 2 and (self.by.get(tok[1]) or self.recipe(tok[1])):
            return ('script', tok[0], tok[1])
        if len(tok) == 2 and tok[1] in ACCENT.values() and (self.by.get(tok[0]) or self.recipe(tok[0])):
            return ('accent', tok[0], tok[1])
        return None

    def make(self, ch, rng):
        t = self._token(ch) if not self.recipe(ch) else None
        if t:
            g = self._get(t[2] if t[0] == 'script' else t[1], rng)
            out = scripted(g, t[1]) if t[0] == 'script' else with_accent(g, t[2], rng, self.by, self.pen)
            if out:
                out['ch'] = ch
            return out
        r = self.recipe(ch)
        if r is None:
            return None
        kind = r[0]
        if kind == 'pen':
            return pen_mark(ch, self.pen)
        if kind == 'j':
            # the hook of the writer's J (without its top bar), dropped below the baseline, and a dot
            J = rng.choice(self.by['J'])
            hook = max(J['strokes'], key=lambda st: np.ptp(np.asarray(st['p'], float)[:, 1]))
            p = np.asarray(hook['p'], float)
            s = (self.low + 0.42) / max(np.ptp(p[:, 1]), 1e-3)
            y0 = p[:, 1].min()
            st = [dict(hook, p=[[x * s, (y - y0) * s - self.low] for x, y in p])]
            hx = min(st[0]['p'], key=lambda q: q[1])[0]  # above the top of the stem
            d = rng.choice(self.by.get('·') or self.by['.'])
            st += _map(d, lambda x, y: (x - d['adv'] / 2 + hx, y - (d['top'] + d['bot']) / 2 - self.low - 0.25))
            return _record(ch, st)
        if kind == 'combine':
            strokes = []
            for chars, cx, cy, size in r[1]:
                g = rng.choice(self.by[self._first(chars)])
                s = _scaled(g, size)
                gx, gy = g['adv'] / 2, (g['top'] + g['bot']) / 2
                strokes += _map(g, lambda x, y: ((x - gx) * s + cx, (y - gy) * s + cy))
            return _record(ch, strokes)
        g = rng.choice(self.by[r[1]])
        if kind == 'alias':
            return dict(g, ch=ch, derived=ch not in SAME)
        if kind == 'fit_cap':
            # same shape, set on the baseline at capital height (Ψ from ψ)
            s = self.cap / max(g['bot'] - g['top'], 1e-3)
            return _record(ch, _map(g, lambda x, y: (x * s, (y - g['bot']) * s)))
        if kind == 'case':
            # same shape, other size: capitals stand on the baseline at cap height, small letters at x-height
            target = self.cap if ch.isupper() else self.low
            s = target / max(-g['top'] if g['bot'] > -0.15 else g['bot'] - g['top'], 1e-3)
            dy = 0.0 if g['bot'] > -0.15 else -g['bot'] * s
            return _record(ch, _map(g, lambda x, y: (x * s, y * s + dy)))
        if kind == 'flip_x':
            return _record(ch, _map(g, lambda x, y: (g['adv'] - x, y)))
        if kind == 'flip_y':
            t, b = g['top'], g['bot']
            return _record(ch, _map(g, lambda x, y: (x, t + b - y)))
        if kind == 'rot':
            a = math.radians(r[2])
            cx, cy = g['adv'] / 2, (g['top'] + g['bot']) / 2
            ca, sa = math.cos(a), math.sin(a)
            st = _map(g, lambda x, y: ((x - cx) * ca - (y - cy) * sa, (x - cx) * sa + (y - cy) * ca))
            # a turned arrow is as tall as a capital and stands on the baseline
            x0, y0, x1, y1 = _strokes_box(st)
            s = self.cap / max(y1 - y0, 1e-3)
            return _record(ch, [dict(q, p=[[x * s, (y - y1) * s] for x, y in q['p']]) for q in st])
        return None


SUB_SCALE, SUB_DROP = 0.55, 0.04      # text-mode subscripts: size and how far their baseline drops
SUP_SCALE, SUP_RISE = 0.4, -0.68


def with_accent(base, mark, rng, by, pen=0.095):
    """A letter with a mark above it (vector arrow, hat, dot, bar) when the notes never show that pair."""
    w = max(base['adv'], 0.3)
    cx, top = base['adv'] / 2, base['top']
    strokes = [dict(s) for s in base['strokes']]
    if mark == '⃗' and by.get('→'):
        a = rng.choice(by['→'])
        s = max(w * 0.95, 0.4) / max(a['adv'], 1e-3)
        ay = (a['top'] + a['bot']) / 2
        strokes += _map(a, lambda x, y: ((x - a['adv'] / 2) * s + cx, (y - ay) * s + top - 0.14))
    elif mark == '̇' and (by.get('·') or by.get('.')):
        d = rng.choice(by.get('·') or by['.'])
        strokes += _map(d, lambda x, y: (x - d['adv'] / 2 + cx, y - (d['top'] + d['bot']) / 2 + top - 0.12))
    else:
        # hat, bar, tilde: one pen line over the letter
        t = np.linspace(0, 1, 16)
        hw = min(w, 0.4) / 2
        if mark == '̂':
            xs, ys = np.array([cx - hw, cx, cx + hw]), np.array([top - .08, top - .24, top - .08])
        elif mark == '̄':
            xs, ys = cx - hw + 2 * hw * t, np.full_like(t, top - .12)
        elif mark == '̃':
            xs, ys = cx - hw + 2 * hw * t, top - .14 - .05 * np.sin(t * 2 * np.pi)
        else:
            return None
        strokes.append({'p': np.c_[xs, ys].tolist(), 'w': pen, 'c': strokes[0]['c']})
    return _record(base['ch'] + mark, strokes)


def scripted(g, kind):
    """A small copy of glyph g set as a text-mode subscript (kind '_') or superscript ('^')."""
    s, dy = (SUB_SCALE, SUB_DROP) if kind == '_' else (SUP_SCALE, SUP_RISE)
    return _record(kind + g['ch'], _map(g, lambda x, y: (x * s, y * s + dy)))
