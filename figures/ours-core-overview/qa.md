# Figure 2 audit

Status: vector figure generated; structural and local PDF visual review passed. Exact venue/template fit and native draw.io-app rendering remain unchecked.

| Check | Result |
|---|---|
| Single method claim | Same primary representation yields DRAM screening and SSD navigation views |
| A contribution | Primary 4-bit MSB extraction is explicit; full primary retained |
| B contribution | Candidate screening controls fine payload requests; scoring occurs on CPU side |
| C contribution | Residual used for final shortlist reranking, not an invented per-node uncertainty gate |
| Scope | Resident routing; real-valued input, current INT8 gate/navigation kernels explained in caption |
| Memory accounting | Factors shown; no claim of 1-bit-only total memory footprint |
| I/O semantics | Multiple candidate records in a page; graph adjacency shown on SSD; no promise of zero graph I/O |
| Accuracy claim | No claim of lossless screening, exact 4-bit distances, or measured speedup |
| Draw.io | XML parsed, roots 0/1 present, IDs unique, native editable cells/edges, no images |
| SVG | Live text and vector geometry, no image or foreignObject elements |
| PDF | One page, embedded TrueType font subsets, selectable labels, zero image objects |
| Typography | Labels fit their specified cells; inspected PDF raster preview at 178 mm layout |
| Layout | Main panel B largest; clear panel dividers; corrected overlapping update/query annotations |
| Palette | Blue for primary/MSB, orange for residual, gray/cross marks for pruned candidates; labels preserve meaning without color |
| Reference handling | ImageGen used only for composition; final vector assets contain no generated-image bitmap |
| Output consistency | SVG/PDF derive from the same native .drawio XML, not separate hand-maintained layouts |

Readability review: MSB extraction is visible first in A; colored candidates in B lead across the boundary to one shared page; C gives each precision level a different role. Full scientific qualifications are in the external caption to keep the artwork readable.

Limitations: this is a local native-shape XML renderer because draw.io Desktop CLI is absent. It does not substitute for a full mxGraph layout engine and supports only the documented subset. For manual additions or compressed diagrams use draw.io's own export. Publication template and print-size requirements have not been supplied, so no venue-compliance certification is claimed.

No data analysis, experiments, or numerical results were altered. Structural checks and visual inspection are appropriate for this figure-only change.
