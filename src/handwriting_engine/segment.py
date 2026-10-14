"""Group a page's strokes into writing runs (left-to-right sequences in time order)."""
import numpy as np

TEXT_COLORS = None  # all colours are kept; colour is stored per stroke


def bbox(s):
    return float(s['x'].min()), float(s['y'].min()), float(s['x'].max()), float(s['y'].max())


def char_height(strokes):
    """Typical character height estimated from small strokes."""
    hs = np.array([bbox(s)[3] - bbox(s)[1] for s in strokes])
    ws = np.array([bbox(s)[2] - bbox(s)[0] for s in strokes])
    m = np.maximum(hs, ws)
    small = m[(m > 3) & (m < np.percentile(m, 90))]
    return float(np.percentile(small, 75)) if len(small) else 20.0


def is_drawing(b, ch, pen=2.76):
    """Figures, boxes, long arrows. Thicker pens are used for bigger titles, so scale with pen width."""
    k = max(1.0, pen / 2.76 * 1.6)
    w, h = b[2] - b[0], b[3] - b[1]
    return h > 2.6 * ch * k or w > 5 * ch * k


def runs(strokes, ch=None):
    """Split strokes (in writing order) into runs. Returns (ch, list of runs);
    each run is a list of stroke indices. Big strokes (figures, boxes, long arrows) get their own run
    flagged later by is_drawing."""
    ch = ch or char_height(strokes)
    out, cur, cb = [], [], None
    for i, s in enumerate(strokes):
        b = bbox(s)
        if is_drawing(b, ch, s['width']):
            if cur:
                out.append(cur)
            out.append([i])
            cur, cb = [], None
            continue
        if cur:
            lch = max(ch, 1.15 * float(np.median([max(bbox(strokes[j])[2] - bbox(strokes[j])[0],
                                                       bbox(strokes[j])[3] - bbox(strokes[j])[1]) for j in cur])))
            cy = (cb[1] + cb[3]) / 2
            sy = (b[1] + b[3]) / 2
            back = b[0] < cb[2] - 2.5 * lch and b[2] < cb[2] - 1.5 * lch   # pen went back left
            jump = abs(sy - cy) > 1.6 * lch or b[0] > cb[2] + 4 * lch     # different line or far right
            if back or jump:
                out.append(cur)
                cur, cb = [], None
        if not cur:
            cur, cb = [i], b
        else:
            cur.append(i)
            cb = (min(cb[0], b[0]), min(cb[1], b[1]), max(cb[2], b[2]), max(cb[3], b[3]))
    if cur:
        out.append(cur)
    return ch, out


def run_box(strokes, r):
    bs = [bbox(strokes[i]) for i in r]
    return (min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs))


def merge_deferred(strokes, ch, R, look=6):
    """Merge a short run into an earlier run when it sits inside that run's box
    (e.g. the user writes all the 받침 of a word after the word)."""
    out = []
    for r in R:
        b = run_box(strokes, r)
        if len(r) == 1 and is_drawing(bbox(strokes[r[0]]), ch, strokes[r[0]]['width']):
            out.append(r)
            continue
        merged = False
        for prev in reversed(out[-look:]):
            if len(prev) == 1 and is_drawing(bbox(strokes[prev[0]]), ch, strokes[prev[0]]['width']):
                continue
            pb = run_box(strokes, prev)
            if b[0] >= pb[0] - 0.3 * ch and b[2] <= pb[2] + 0.3 * ch and b[1] >= pb[1] - 0.6 * ch and b[3] <= pb[3] + 0.9 * ch:
                prev.extend(r)
                merged = True
                break
        if not merged:
            out.append(list(r))
    return out


def _is_bar(b, ch):
    w, h = b[2] - b[0], b[3] - b[1]
    return w > 0.9 * ch and h < 0.2 * w and h < 0.35 * ch


def split_stacked(strokes, ch, r):
    """Cut a run that holds fractions into pieces without stacked parts: the line itself (fraction
    bars removed) and each numerator and denominator, recursively. Returns [] when there is nothing
    to cut."""
    bars = []
    for i in r:
        b = bbox(strokes[i])
        if not _is_bar(b, ch):
            continue
        y = (b[1] + b[3]) / 2
        up, down = [], []
        for j in r:
            if j == i:
                continue
            c = bbox(strokes[j])
            cx, cy = (c[0] + c[2]) / 2, (c[1] + c[3]) / 2
            if b[0] - 0.1 * ch <= cx <= b[2] + 0.1 * ch:
                if cy < y and c[3] > y - 2.5 * ch:
                    up.append(j)
                elif cy > y and c[1] < y + 2.5 * ch:
                    down.append(j)
        if up and down:
            top = min(bbox(strokes[j])[1] for j in up)
            bot = max(bbox(strokes[j])[3] for j in down)
            # a fraction is taller than one line; a Hangul ㅡ with jamo above and below is not
            if bot - top <= 1.7 * ch:
                continue
            # numerator and denominator are real characters (a vector arrow's head is not)
            hu = max(bbox(strokes[j])[3] for j in up) - top
            hd = bot - min(bbox(strokes[j])[1] for j in down)
            if hu < 0.45 * ch or hd < 0.45 * ch:
                continue
            rest = [bbox(strokes[j]) for j in r if j != i and j not in up and j not in down]
            if len(rest) >= 3:
                # the bar sits mid-line and the stacked parts reach above and below the line
                mt = float(np.percentile([c[1] for c in rest], 10))
                mb = float(np.percentile([c[3] for c in rest], 90))
                mc, mh = (mt + mb) / 2, max(mb - mt, 0.5 * ch)
                if abs(y - mc) > 0.6 * mh or top > mt - 0.2 * ch or bot < mb + 0.2 * ch:
                    continue
            bars.append((b[2] - b[0], i, up, down))
    if not bars:
        return []
    _, i, up, down = max(bars)
    taken = set(up) | set(down) | {i}
    rest = [j for j in r if j not in taken]
    out = []
    for part in (rest, up, down):
        if part:
            sub = split_stacked(strokes, ch, part)
            out.extend(sub if sub else [part])
    return out


def page_runs(strokes, pieces=True):
    """Runs of a page. With pieces=True, runs that contain fractions are followed (at the END of the list,
    so earlier run ids never change) by their pieces from split_stacked."""
    ch, R = runs(strokes)
    R = merge_deferred(strokes, ch, R)
    if pieces:
        extra = []
        for r in R:
            if len(r) < 3:
                continue
            for part in split_stacked(strokes, ch, r):
                if sorted(part) != sorted(r):
                    extra.append(sorted(part))
        R = R + extra
    return ch, R
