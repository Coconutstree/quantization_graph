"""Check manuscript cross-files and the newly written algebraic identities.

Numerical checks are sanity checks of equations, not performance experiments
or proofs of the implemented ANN search's recall.
"""
from pathlib import Path
import collections
import hashlib
import json
import re
import numpy as np

ROOT = Path(__file__).resolve().parent


def main():
    text = (ROOT / 'manuscript.md').read_text().split('## References')[0]
    bib = (ROOT / 'references.bib').read_text()
    keys = set(re.findall(r'@\w+\{([\w-]+),', bib))
    used = set(re.findall(r'@([a-z][\w-]+)', text))
    assert used <= keys, used - keys
    assert text.count('$$') % 2 == 0
    tex = '\n'.join((ROOT / p).read_text() for p in ['main.tex', 'body.tex', 'overview.tex'])
    assert set(re.findall(r'\\cite\{([^}]+)\}', tex))
    tex_keys = {k for group in re.findall(r'\\cite\{([^}]+)\}', tex) for k in group.split(',')}
    assert tex_keys == used
    begin = collections.Counter(re.findall(r'\\begin\{([^}]+)\}', tex))
    end = collections.Counter(re.findall(r'\\end\{([^}]+)\}', tex))
    assert begin == end, (begin, end)
    assert '[@' not in tex
    assert r'\documentclass[sigconf,anonymous]{acmart}' in tex
    assert tex.count(r'\begin{equation}') == text.count('$$') // 2
    # Checks are in float64 and deliberately do not simulate the native encoder.
    rng = np.random.default_rng(20260914)
    max_identity_error = 0.0
    trials = 512
    for _ in range(trials):
        d = int(rng.choice([8, 16, 64, 128]))
        z, v, y = rng.normal(size=(3, d))
        def approx(a):
            h = np.sign(a) * (rng.integers(0, 8, size=d) + 0.5)
            return (a @ a) / (a @ h) * h
        zx, vv = approx(z), approx(v)
        ax, av = zx-z, vv-v
        exact = np.sum((z-v)**2)
        raw = z@z + v@v - 2*(zx@vv)
        expansion = -2*(z@av + v@ax + ax@av)
        error = abs((raw-exact) - expansion)
        max_identity_error = max(max_identity_error, error)
        assert np.isclose(raw-exact, expansion)
        assert abs(z@ax) < 1e-9
        normprod = np.linalg.norm(z)*np.linalg.norm(v)
        clipped = z@z + v@v - 2*np.clip(zx@vv, -normprod, normprod)
        bound = 2*(np.linalg.norm(z)*np.linalg.norm(av) +
                   np.linalg.norm(v)*np.linalg.norm(ax) + np.linalg.norm(ax)*np.linalg.norm(av))
        assert abs(clipped-exact) <= bound + 1e-9
        corr = rng.normal(size=d)*0.01
        corrected = y@y + z@z - 2*y@(zx+corr)
        exactq = np.sum((y-z)**2)
        assert np.isclose(corrected-exactq, -2*y@(zx+corr-z))
        yhat = y + rng.normal(size=d)*0.01
        traverse = y@y + z@z - 2*yhat@zx
        assert np.isclose(traverse-exactq, -2*y@(zx-z)-2*(yhat-y)@zx)
        labels = rng.integers(0,16,size=d)
        b, ell = labels//8, labels%8
        assert np.isclose(yhat@(labels-7.5), 8*(yhat@(b-0.5)) + yhat@(ell-3.5))
    assert 9*1024//8+25 == 1177
    assert 4096//1177 == 3 and 4096-3*1177 == 565
    sections = re.split(r'^## ', text, flags=re.M)[1:]
    counts = {s.splitlines()[0]: len(re.findall(r'\b[\w-]+\b', s)) for s in sections}
    report = {
        'citation_keys': sorted(used), 'citation_consistency': 'passed',
        'latex_environment_balance': 'passed',
        'display_equations': text.count('$$')//2,
        'algebra_trials': trials, 'max_symmetric_identity_abs_error': max_identity_error,
        'algebra_check_scope': 'float64 identity and inequality sanity checks; not native search validation',
        'section_token_like_word_counts': counts,
        'total_token_like_word_count': sum(counts.values()),
        'evidence_placeholders': sorted(set(re.findall(r'\bE[1-5]\b', text))),
        'pdf_compiled': False, 'page_count_verified': False,
        'manuscript_sha256': hashlib.sha256((ROOT/'manuscript.md').read_bytes()).hexdigest(),
    }
    pdf = ROOT / 'main.pdf'
    inputs = ['main.tex', 'body.tex', 'overview.tex', 'references.bib', 'manuscript.md']
    if pdf.exists() and pdf.stat().st_mtime >= max((ROOT / p).stat().st_mtime for p in inputs):
        from pypdf import PdfReader
        reader = PdfReader(pdf)
        report.update(pdf_compiled=True, page_count_verified=True,
                      pdf_pages=len(reader.pages), pdf_bytes=pdf.stat().st_size,
                      pdf_sha256=hashlib.sha256(pdf.read_bytes()).hexdigest(),
                      pdf_check_scope='Parsed pages and text; not a visual submission-readiness review')
    else:
        report['reason'] = 'No current PDF found; run build.py before PDF checks'
    (ROOT/'validation.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
