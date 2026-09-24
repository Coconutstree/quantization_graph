# Visual QA

fig01_encoding_storage: width 183.000 mm; PDF glyph audit passed, minimum 7 pt.

fig02_query_flow: width 183.000 mm; PDF glyph audit passed, minimum 7 pt.

fig03_page_reuse: width 183.000 mm; PDF glyph audit passed, minimum 7 pt.

Source preflight: zero failures. The width warning is a parser false positive on `183/25.4`; actual PDF MediaBox confirms 183 mm.

Panel checks: encoding anatomy and residency labels separated; no missing glyphs after mathtext fix. Query flow: decision diamond, fallback, loop and final rerank are legible. Page diagram: surviving ID 4 points to record 4; cache result conditional on retention. All figures inspected as matplotlib PNG exports. Illustrative examples only; no statistical uncertainty applicable.
