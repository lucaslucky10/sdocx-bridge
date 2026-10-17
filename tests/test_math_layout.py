"""Handwritten math layout: fractions, roots, scripts and slashes."""
import random

import numpy as np

from handwriting_engine.compose import GlyphDB, grow_brackets, math_strokes, slash_fractions, written_fractions
from sdocx_bridge.handwriting import load_db


def _db(lib):
    return load_db(lib, "my-handwriting", synthesize=False)


def test_slash_becomes_a_fraction():
    assert slash_fractions(r"2e^{-x}/(1-e^{-x})") == r"\frac{2e^{-x}}{1-e^{-x}}"
    assert slash_fractions(r"[\theta]_0^{\pi/4}") == r"[\theta]_0^{\frac{\pi}{4}}"
    assert slash_fractions(r"y=-x/2+1") == r"y=-\frac{x}{2}+1"
    assert slash_fractions(r"f(x)/g(x)") == r"\frac{f(x)}{g(x)}"
    assert slash_fractions(r"1/\ln 3") == r"\frac{1}{\ln 3}"
    assert slash_fractions(r"\sqrt{3}/2") == r"\frac{\sqrt{3}}{2}"
    assert slash_fractions(r"a+b") == r"a+b"
    assert written_fractions("cos α/2 = 1/3 이므로 $x/4$") == r"cos $\frac{\alpha}{2}$ = $\frac{1}{3}$ 이므로 $\frac{x}{4}$"
    assert written_fractions("분자/분모") == "분자/분모"


def test_brackets_around_a_fraction_grow():
    assert grow_brackets(r"f(\frac{1}{2})+(x+1)") == r"f\left(\frac{1}{2}\right)+(x+1)"
    assert grow_brackets(r"\left(\frac12\right)") == r"\left(\frac12\right)"
    assert grow_brackets(r"{(}\frac12)") == r"{(}\frac12)"


def _boxes(strokes):
    return [(float(np.min(s["x"])), float(np.max(s["x"])), float(np.min(s["y"])), float(np.max(s["y"])))
            for s in strokes]


def test_numerator_is_spaced_like_handwriting(lib):
    """TeX's wide spaces around + inside a numerator must not survive under a fraction bar."""
    db = _db(lib)
    tight, w1 = math_strokes(db, r"A+B", random.Random(1), 0, 1)
    frac, w2 = math_strokes(db, r"\frac{A+B}{123123123}", random.Random(1), 0, 1)
    top = [b for b in _boxes(frac) if b[3] < 0.5]  # strokes above the bar
    width_num = max(b[1] for b in top) - min(b[0] for b in top)
    assert abs(width_num - 0.88 * w1) < 0.15  # a fraction is written a little smaller than the line


def test_fraction_bar_keeps_clear_of_signs(lib):
    db = _db(lib)
    st, _ = math_strokes(db, r"A=\frac{1}{2}+B", random.Random(3), 0, 1)
    bars = [s for s in st if len(s["x"]) == 12 and np.ptp(s["y"]) < 0.05]  # pen_line
    assert len(bars) == 1
    a, b = float(np.min(bars[0]["x"])), float(np.max(bars[0]["x"]))
    others = [bx for bx in _boxes(st) if bx[1] < a or bx[0] > b]
    left = max(bx[1] for bx in others if bx[1] < a)
    right = min(bx[0] for bx in others if bx[0] > b)
    assert a - left > 0.1 and right - b > 0.1
