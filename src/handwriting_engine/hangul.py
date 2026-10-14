"""Make Hangul syllables that never appear in the notes from the writer's own jamo.

1. Every collected syllable is split into its initial consonant, vowel and final consonant. Strokes are
   kept in writing order, and Hangul is written 초성 -> 중성 -> 종성, so the split is two cut points in that
   order; the cuts are chosen so the three parts sit where that syllable shape puts them. The expected
   regions start as rough templates and are then re-estimated from the data (two rounds).
2. A missing syllable takes each part from a collected syllable that has the same jamo in the same
   syllable shape, and moves it into the region where this writer usually puts that jamo.
"""
import random

import numpy as np

CHO = 'ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ'
JUNG = 'ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ'
JONG = ' ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ'
VERT = set('ㅏㅐㅑㅒㅓㅔㅕㅖㅣ')
HORI = set('ㅗㅛㅜㅠㅡ')

# rough first-guess regions (x0, y0, x1, y1) inside the syllable box, per shape
KINDS = ('V', 'O', 'U', 'CO', 'CU', 'CI')
RELATED = {'V': (), 'O': ('U',), 'U': ('O',), 'CO': ('CU', 'CI'), 'CU': ('CO', 'CI'), 'CI': ('CO', 'CU')}
_TEMPLATE = {
    ('V', 0): {'cho': (0, 0, .6, 1), 'jung': (.45, 0, 1, 1)},
    ('V', 1): {'cho': (0, 0, .6, .6), 'jung': (.45, 0, 1, .7), 'jong': (.1, .6, .9, 1)},
    ('H', 0): {'cho': (.1, 0, .9, .55), 'jung': (0, .45, 1, 1)},
    ('H', 1): {'cho': (.1, 0, .9, .4), 'jung': (0, .3, 1, .65), 'jong': (.1, .6, .9, 1)},
    ('C', 0): {'cho': (0, 0, .6, .5), 'jung': (0, .3, 1, 1)},
    ('C', 1): {'cho': (0, 0, .6, .4), 'jung': (0, .25, 1, .7), 'jong': (.1, .65, .9, 1)},
}
TEMPLATE = {(k, f): _TEMPLATE[('V' if k == 'V' else 'H' if k in ('O', 'U') else 'C', f)] for k in KINDS for f in (0, 1)}
PARTS = ('cho', 'jung', 'jong')
PAIRS = {'ㄲ': 'ㄱㄱ', 'ㄸ': 'ㄷㄷ', 'ㅃ': 'ㅂㅂ', 'ㅆ': 'ㅅㅅ', 'ㅉ': 'ㅈㅈ', 'ㄳ': 'ㄱㅅ', 'ㄵ': 'ㄴㅈ',
         'ㄶ': 'ㄴㅎ', 'ㄺ': 'ㄹㄱ', 'ㄻ': 'ㄹㅁ', 'ㄼ': 'ㄹㅂ', 'ㄽ': 'ㄹㅅ', 'ㄾ': 'ㄹㅌ', 'ㄿ': 'ㄹㅍ', 'ㅀ': 'ㄹㅎ',
         'ㅄ': 'ㅂㅅ'}
TICK_LEFT = set('ㅓㅔㅕㅖ')
TICKS_NEEDED = {'ㅓ': 1, 'ㅔ': 1, 'ㅕ': 2, 'ㅖ': 2}
# how far a donor part moves from where it sat in its own syllable toward where this writer usually puts
# that jamo (position, size); tuned with tools/jamo_test.py
PULL, PULL_SIZE = 0.75, 0.5
EXTRA_BAR = {'ㅐ': 'ㅏ', 'ㅒ': 'ㅑ', 'ㅔ': 'ㅓ', 'ㅖ': 'ㅕ', 'ㅙ': 'ㅘ', 'ㅞ': 'ㅝ'}


def decompose(ch):
    k = ord(ch) - 0xAC00
    return CHO[k // 588], JUNG[(k % 588) // 28], JONG[k % 28].strip()


def compose_char(cho, jung, jong=''):
    return chr(0xAC00 + CHO.index(cho) * 588 + JUNG.index(jung) * 28 + JONG.index(jong or ' '))


def shape(ch):
    """Syllable layout, as Hangul type designers group it: vowel on the right (V), a bottom vowel with a
    flat bar (O: ㅗ ㅛ ㅡ) or with legs hanging down (U: ㅜ ㅠ), and the two-part vowels built on ㅗ (CO),
    ㅜ (CU) or ㅡ (CI); with or without a final consonant. The initial consonant is drawn differently in
    each (a big ㅇ over ㅡ, a small one over ㅜ), so parts are only swapped within the same layout."""
    _, v, f = decompose(ch)
    if v in VERT:
        k = 'V'
    elif v in HORI:
        k = 'U' if v in 'ㅜㅠ' else 'O'
    else:
        k = 'CU' if v in 'ㅝㅞㅟ' else 'CI' if v == 'ㅢ' else 'CO'
    return (k, int(bool(f)))



def count_ticks(ss):
    """Short bars on the left of a vowel's stem (ㅓ has one, ㅕ two)."""
    tall = [st for st in ss if np.ptp(np.asarray(st['p'])[:, 1]) > 0.35]
    if not tall:
        return 0
    vx = min(float(np.min(np.asarray(st['p'])[:, 0])) for st in tall)
    n = 0
    for st in ss:
        p = np.asarray(st['p'])
        if np.ptp(p[:, 1]) < 0.22 and (p[:, 0].min() + p[:, 0].max()) / 2 < vx + 0.02:
            n += 1
    return n


def _box(strokes):
    p = np.concatenate([np.asarray(s['p'], float) for s in strokes])
    return p[:, 0].min(), p[:, 1].min(), p[:, 0].max(), p[:, 1].max()


def _norm(b, g):
    """Box b in the glyph's own unit square (0..1 over its bounding box)."""
    W, H = max(g['adv'], 1e-6), max(g['bot'] - g['top'], 1e-6)
    return ((b[0]) / W, (b[1] - g['top']) / H, (b[2]) / W, (b[3] - g['top']) / H)


def _dist(a, b):
    return sum(abs(x - y) for x, y in zip(a, b))


def split(g, regions, nstrokes=None, jamo=None):
    """Cut a syllable's strokes (in writing order) into parts. Returns ({part: [strokes]}, cost).
    nstrokes: {(part, jamo): usual number of strokes} -- a soft prior once it has been learned."""
    S = g['strokes']
    n = len(S)
    parts = [p for p in PARTS if p in regions]
    if n < len(parts):
        return None, float('inf')
    boxes = {}

    def cost(i, j, part):
        if (i, j) not in boxes:
            boxes[(i, j)] = _norm(_box(S[i:j]), g)
        v = _dist(boxes[(i, j)], regions[part])
        if nstrokes and jamo and (part, jamo[part]) in nstrokes:
            v += 0.2 * abs((j - i) - nstrokes[(part, jamo[part])])
        return v

    best, arg = float('inf'), None
    if len(parts) == 2:
        for c in range(1, n):
            v = cost(0, c, 'cho') + cost(c, n, 'jung')
            if v < best:
                best, arg = v, (c, n)
    else:
        for c1 in range(1, n - 1):
            for c2 in range(c1 + 1, n):
                v = cost(0, c1, 'cho') + cost(c1, c2, 'jung') + cost(c2, n, 'jong')
                if v < best:
                    best, arg = v, (c1, c2)
    cuts = list((0,) + arg + (n,))
    if jamo and jamo['jung'] in TICK_LEFT:
        # ㅓ ㅔ ㅕ ㅖ: the short bar(s) left of the vowel's stem are written right after the initial
        # consonant, and the region fit tends to hand them to it; give them back to the vowel
        W, H = max(g['adv'], 1e-6), max(g['bot'] - g['top'], 1e-6)
        tall = [st for st in S[cuts[1]:cuts[2]] if np.ptp(np.asarray(st['p'])[:, 1]) > 0.4 * H] or S[cuts[1]:cuts[2]]
        vx = min(float(np.min(np.asarray(st['p'])[:, 0])) for st in tall)  # the (first) stem
        for _ in range(2):
            if cuts[1] <= 1:
                break
            b = _box([S[cuts[1] - 1]])
            if b[3] - b[1] < 0.2 * H and b[2] - b[0] < 0.4 * W and b[2] >= vx - 0.2 * W and b[0] > 0.15 * W:
                cuts[1] -= 1
            else:
                break
    return {p: S[cuts[k]:cuts[k + 1]] for k, p in enumerate(parts)}, best / len(parts)


class JamoBank:
    """Parts of the writer's syllables, indexed by (part, jamo, shape)."""

    def __init__(self, glyphs_by_char, max_cost=0.9):
        syl = [(c, g) for c, gs in glyphs_by_char.items() if len(c) == 1 and '가' <= c <= '힣' for g in gs]
        regions = {k: dict(v) for k, v in TEMPLATE.items()}
        jregions, nstrokes = {}, None
        for _ in range(4):
            done = []
            for c, g in syl:
                jam = dict(zip(PARTS, decompose(c)))
                reg = {p: jregions.get((shape(c), p, jam[p]), r) for p, r in regions[shape(c)].items()}
                sp, cost = split(g, reg, nstrokes, jam)
                if sp is not None:
                    done.append((c, g, sp, cost))
            # re-estimate regions per shape and per (shape, part, jamo), and usual stroke counts
            acc, accj, cnt = {}, {}, {}
            for c, g, sp, cost in done:
                if cost > max_cost:
                    continue
                jam = dict(zip(PARTS, decompose(c)))
                for p, ss in sp.items():
                    b = _norm(_box(ss), g)
                    acc.setdefault((shape(c), p), []).append(b)
                    accj.setdefault((shape(c), p, jam[p]), []).append(b)
                    cnt.setdefault((p, jam[p]), []).append(len(ss))
            for (shp, p), bs in acc.items():
                if len(bs) >= 3:
                    regions[shp][p] = tuple(np.median(np.array(bs), 0))
            jregions = {k: tuple(np.median(np.array(v), 0)) for k, v in accj.items() if len(v) >= 3}
            nstrokes = {k: float(np.median(v)) for k, v in cnt.items() if len(v) >= 3}
        self.regions = regions
        self.exclude = set()
        self.jamo_regions = {k: tuple(np.median(np.array(v), 0)) for k, v in accj.items()}
        self.nstrokes = nstrokes
        self.items = {}
        self.splits = done
        # the short bars of ㅓ ㅔ ㅕ ㅖ: how many each part carries (some writers join the bar to ㅇ, so
        # it stays with the initial); a rebuilt syllable must end up with exactly as many as its vowel needs
        self.ticks = {}
        for c, g, sp, cost in done:
            if cost > max_cost:
                continue
            jam = dict(zip(PARTS, decompose(c)))
            if jam['jung'] in TICK_LEFT:
                jt = count_ticks(sp['jung'])
                self.ticks[id(sp['jung'])] = jt
                self.ticks[id(sp['cho'])] = max(0, TICKS_NEEDED[jam['jung']] - jt)
            for p, ss in sp.items():
                self.items.setdefault((p, jam[p], shape(c)), []).append((c, g, ss))
        self.size = {shp: (float(np.median([g['adv'] for c, g, _, _ in done if shape(c) == shp] or [0.6])),
                           float(np.median([g['bot'] - g['top'] for c, g, _, _ in done if shape(c) == shp] or [1.0])),
                           float(np.median([g['bot'] for c, g, _, _ in done if shape(c) == shp] or [0.0])))
                     for shp in TEMPLATE}

    def _donors(self, part, jamo, shp):
        """Same jamo in the same shape; else the same jamo in a related shape. Syllables listed in
        self.exclude never donate (tools/jamo_test.py rebuilds a syllable without its own samples)."""
        k, f = shp
        near = [(r, f) for r in RELATED[k]] + [(r, 1 - f) for r in RELATED[k]]
        rest = [(r, ff) for r in KINDS if r != k and r not in RELATED[k] for ff in (f, 1 - f)]
        tries = [shp]
        if part == 'cho':
            tries += [(k, 1 - f)] + near + rest
        elif part == 'jung':
            tries += [(k, 1 - f)]
        else:
            tries += [(r, 1) for r in RELATED[k]] + [(r, 1) for r in KINDS if r != k and r not in RELATED[k]]
        for t in tries:
            got = [d for d in self.items.get((part, jamo, t), ()) if d[0] not in self.exclude]
            if got:
                return t, got
        return None, []

    def _part(self, part, jamo, shp, rng=None, want=None):
        """Strokes for one part: (strokes, their box, donor syllable or None) or None. A donor is given
        when the part comes from a syllable of the same shape, so it can keep the place it had there."""
        dshape, donors = self._donors(part, jamo, shp)
        if donors:
            if rng is None:
                return [], None, None
            if want and dshape == shp:
                # best: a syllable that shares the neighbouring jamo too (ㅂ for 비 from 빈, not from 바),
                # so the part sits right next to that neighbour
                other = 'jung' if part != 'jung' else 'cho'
                same = [d for d in donors if decompose(d[0])[PARTS.index(other)] == want[other]]
                if same and rng.random() < 0.85:
                    donors = same
            c, g, ss = rng.choice(donors)
            return ss, _box(ss), g if dshape == shp else None
        if part == 'jong' and jamo in CHO:
            # a final consonant never written as a final: use the same letter written as an initial
            for t in [(k, f) for k in KINDS for f in (1, 0)]:
                got = [d for d in self.items.get(('cho', jamo, t), ()) if d[0] not in self.exclude]
                if got:
                    if rng is None:
                        return [], None, None
                    c, g, ss = rng.choice(got)
                    return ss, _box(ss), None
        if part == 'jung' and jamo in EXTRA_BAR:
            # ㅐ ㅒ ㅔ ㅖ ㅙ ㅞ = the vowel without the second bar + a copy of its long vertical stroke
            got = self._part(part, EXTRA_BAR[jamo], shp, rng)
            if got is None or rng is None:
                return got
            ss, bx, _ = got
            tall = max(ss, key=lambda st: np.ptp(np.asarray(st['p'])[:, 1]))
            q = np.asarray(tall['p'], float)
            dx = 0.18 * (bx[3] - bx[1])
            ss = list(ss) + [{**tall, 'p': np.stack([q[:, 0] + dx, q[:, 1]], 1).tolist()}]
            return ss, _box(ss), None
        if jamo in PAIRS:
            # double / compound consonant: two single letters side by side, each written narrower
            a, b = PAIRS[jamo]
            A, B = self._part(part, a, shp, rng), self._part(part, b, shp, rng)
            if A is None or B is None:
                return None
            if rng is None:
                return [], None, None
            out, x = [], 0.0
            h = max(A[1][3] - A[1][1], B[1][3] - B[1][1])
            narrow = 0.62 if a == b else 0.75
            for ss, bx, _ in (A, B):
                k = min(h / max(bx[3] - bx[1], 1e-6), 1.3)
                for st in ss:
                    q = np.asarray(st['p'], float)
                    q = np.stack([x + (q[:, 0] - bx[0]) * k * narrow, (q[:, 1] - bx[1]) * k], 1)
                    out.append({**st, 'p': q.tolist()})
                x += (bx[2] - bx[0]) * k * narrow + 0.05
            return out, _box(out), None
        return None

    def can_make(self, ch):
        shp = shape(ch)
        jam = dict(zip(PARTS, decompose(ch)))
        return all(self._part(p, jam[p], shp) is not None for p in PARTS if jam[p])

    def make(self, ch, rng=random, tries=10):
        """A new glyph record for syllable ch, or None. Several random combinations of donor parts are
        tried and the one whose parts sit best together (least ink collision, parts near where this writer
        puts them) is kept."""
        best = None
        for _ in range(tries):
            got = self._assemble(ch, rng)
            if got is None:
                return None
            if best is None or got[1] < best[1]:
                best = got
        return best[0]

    def _assemble(self, ch, rng):
        shp = shape(ch)
        jam = dict(zip(PARTS, decompose(ch)))
        W, H, bot = self.size[shp]
        top = bot - H
        out, placed, cost, ticks = [], [], 0.0, 0
        for p in PARTS:
            if not jam[p]:
                continue
            got = self._part(p, jam[p], shp, rng, want=jam)
            if got is None:
                return None
            ss, src, donor = got
            if p == 'cho':
                ticks += self.ticks.get(id(ss), 0)
            elif p == 'jung' and jam['jung'] in TICK_LEFT:
                ticks += self.ticks.get(id(ss), count_ticks(ss))
            reg = self.jamo_regions.get((shp, p, jam[p])) or self.regions[shp][p]
            if donor is not None:
                # where the part sat in its own syllable, pulled halfway to where it usually goes
                nb = _norm(src, donor)
                a, z = PULL, PULL_SIZE
                cx = (1 - a) * (nb[0] + nb[2]) / 2 + a * (reg[0] + reg[2]) / 2
                cy = (1 - a) * (nb[1] + nb[3]) / 2 + a * (reg[1] + reg[3]) / 2
                hw = 0.5 * ((1 - z) * (nb[2] - nb[0]) + z * (reg[2] - reg[0]))
                hh = 0.5 * ((1 - z) * (nb[3] - nb[1]) + z * (reg[3] - reg[1]))
                r = (cx - hw, cy - hh, cx + hw, cy + hh)
            else:
                # keep the donor's own proportions, centred in the target region
                r = self.regions[shp][p]
                rw, rh = r[2] - r[0], r[3] - r[1]
                sw, sh = (src[2] - src[0]) / W, (src[3] - src[1]) / H
                # height from the region; width may spill past it (a wide ㄲ over ㅡ), within the syllable
                k = min(rh / max(sh, 1e-6), 1.0, max(rw, 0.9 if p != 'jung' and shp[0] != 'V' else rw) / max(sw, 1e-6))
                cx, cy = (r[0] + r[2]) / 2, (r[1] + r[3]) / 2
                cx = min(max(cx, sw * k / 2 + 0.02), 0.98 - sw * k / 2)
                r = (cx - sw * k / 2, cy - sh * k / 2, cx + sw * k / 2, cy + sh * k / 2)
            cost += abs((r[0] + r[2]) / 2 - (reg[0] + reg[2]) / 2) + abs((r[1] + r[3]) / 2 - (reg[1] + reg[3]) / 2)
            tx0, ty0, tx1, ty1 = r[0] * W, top + r[1] * H, r[2] * W, top + r[3] * H
            sx = (tx1 - tx0) / max(src[2] - src[0], 1e-6)
            sy = (ty1 - ty0) / max(src[3] - src[1], 1e-6)
            # thin parts (ㅡ, ㅣ) would be blown up along their thin side: keep that side's scale moderate
            if src[2] - src[0] < 0.12:
                sx = min(sx, sy)
            if src[3] - src[1] < 0.12:
                sy = min(sy, sx)
            part = []
            for s in ss:
                q = np.asarray(s['p'], float)
                q = np.stack([tx0 + (q[:, 0] - src[0]) * sx, ty0 + (q[:, 1] - src[1]) * sy], 1)
                part.append({**s, 'p': q.tolist()})
            ink = np.concatenate([np.asarray(s['p']) for s in part])
            for other in placed:
                d = np.hypot(ink[:, None, 0] - other[None, :, 0], ink[:, None, 1] - other[None, :, 1]).min(1)
                cost += 3.0 * float(np.mean(d < 0.04))
            placed.append(ink)
            out += part
        # a missing bar turns 세 into 시; a bar carried over by ㅇ from 에 puts one into 이
        cost += 5.0 * abs(ticks - TICKS_NEEDED.get(jam['jung'], 0))
        b = _box(out)
        for s in out:
            s['p'] = (np.asarray(s['p']) - [b[0], 0]).tolist()
        return {'ch': ch, 'src': 'synth', 'adv': b[2] - b[0], 'top': b[1], 'bot': b[3], 'strokes': out,
                'synth': True}, cost
