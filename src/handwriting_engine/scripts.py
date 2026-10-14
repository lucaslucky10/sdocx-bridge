"""Sub- and superscripts.  In labels and glyph keys a subscript x is the token '_x', a superscript '^x'.
Unicode forms (₀ ᵢ ² ⁿ ...) are accepted and converted, so 'ω₀' and 'ω_0' mean the same."""
import unicodedata


def _script_map(kind):
    """'x' -> its Unicode subscript/superscript form, for every character that has one."""
    out = {}
    names = {'SUBSCRIPT': ['SUBSCRIPT LATIN SMALL LETTER {L}', 'LATIN SUBSCRIPT SMALL LETTER {L}',
                           'SUBSCRIPT {D}', 'GREEK SUBSCRIPT SMALL LETTER {G}'],
             'SUPERSCRIPT': ['MODIFIER LETTER SMALL {L}', 'SUPERSCRIPT LATIN SMALL LETTER {L}',
                             'SUPERSCRIPT {D}', 'MODIFIER LETTER CAPITAL {L}', 'MODIFIER LETTER SMALL {G}']}[kind]
    digits = 'ZERO ONE TWO THREE FOUR FIVE SIX SEVEN EIGHT NINE'.split()
    cands = [(c, {'L': c.upper()}) for c in 'abcdefghijklmnopqrstuvwxyz']
    cands += [(c.upper(), {'L': c.upper(), 'cap': True}) for c in 'abcdefghijklmnopqrstuvwxyz']
    cands += [(str(i), {'D': d}) for i, d in enumerate(digits)]
    cands += [('+', {'D': 'PLUS SIGN'}), ('-', {'D': 'MINUS'}), ('=', {'D': 'EQUALS SIGN'}),
              ('(', {'D': 'LEFT PARENTHESIS'}), (')', {'D': 'RIGHT PARENTHESIS'})]
    cands += [(c, {'G': unicodedata.name(c).split()[-1]}) for c in 'αβγθφχ']
    for c, kw in cands:
        for pat in names:
            if ('{L}' in pat and 'L' not in kw) or ('{D}' in pat and 'D' not in kw) \
                    or ('{G}' in pat and 'G' not in kw):
                continue
            if ('CAPITAL' in pat) != bool(kw.get('cap')):
                continue
            try:
                out[c] = unicodedata.lookup(pat.format(**{k: v for k, v in kw.items() if k != 'cap'}))
                break
            except KeyError:
                pass
    return out


SUBSCRIPT = _script_map('SUBSCRIPT')
SUPERSCRIPT = _script_map('SUPERSCRIPT')


FROM_UNICODE = {v: '_' + k for k, v in SUBSCRIPT.items()}
FROM_UNICODE.update({v: '^' + k for k, v in SUPERSCRIPT.items()})
for _c, _k in [('ᵢ', '_i'), ('ᵣ', '_r'), ('ₚ', '_p'), ('ₜ', '_t')]:
    FROM_UNICODE[_c] = _k


def is_script_token(tok):
    return len(tok) >= 2 and tok[0] in '_^'
