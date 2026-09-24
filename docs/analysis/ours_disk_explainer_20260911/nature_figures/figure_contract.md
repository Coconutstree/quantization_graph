# Figure revision contract

Backend: saved Python preference; matplotlib for every visual and export.
Archetype: schematic-led composites, white background, direct labels.
Audience: Chinese-speaking readers learning the implemented disk ANN method.
Export: 183 mm wide; editable SVG/PDF; PNG/TIFF 600 dpi; minimum font 7 pt.
No target-journal submission is claimed.

1. Encoding/storage: a single 4-bit code yields a resident MSB copy; residual4 is separate error information. Panel a explains code anatomy, panel b explains residency.
2. Query flow: gate decisions precede payload I/O, followed by candidate-pool updates and a traversal loop. Invalid gate estimates fall back to floating-point scoring. Final reranking uses floating-point query coordinates.
3. Page reuse: three surviving records can share two pages; a rejected record need not remove a page request. Panel b explains conditional cache hits during reranking.

Evidence: the existing method explanation and its recorded implementation sources.
All shown code bits and IDs are teaching examples. They are not measured data.
Statistics, error bars, sample exclusions and experimental inference are not applicable.
Blue denotes in-memory scoring; ochre denotes disk I/O or rejected candidates, with explicit labels; teal denotes final reranking/cache reuse. Shape, labels and connections carry the same semantics without colour.
