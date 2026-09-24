# Literature and format verification — 2026-09-14

| Key | Primary source consulted | Scope |
| --- | --- | --- |
| symphonyqg | https://arxiv.org/html/2411.12229v1 | Introduction, preliminaries, methodology: in-memory scope; edge-local codes; FastScan; implicit reranking; construction alignment. Author metadata from arXiv. |
| saq | https://arxiv.org/html/2509.12086v1 | Introduction, code-adjustment analysis, PCA segmentation and progressive/multi-stage estimation; author list verified. Ignore placeholder publication DOI and conference metadata in the preprint template. |
| rabitq | https://arxiv.org/abs/2405.12497 | Authors, title, year, low-bit representation and theoretical-estimator scope. No inherited end-to-end guarantee asserted. |
| exrabitq | https://arxiv.org/abs/2409.09913 | Authors, title, year, multi-bit extension; representation also cross-checked with the project's code and the SAQ preliminaries. |
| diskann | https://proceedings.neurips.cc/paper/2019/hash/09853c7fb1d3f8ee67a61b6bf4a7f8e6-Abstract.html | Official publication metadata. Official PDF search extraction supports SSD/Vamana context; full PDF open timed out. Local Vamana pruning code checked independently. |
| lvq | https://arxiv.org/abs/2304.04759 | Five authors, title, 2023 preprint, abstract and LVQ scope; no numerical baseline advantage borrowed. |
| nsg | https://arxiv.org/abs/1707.00143 | Four authors, original preprint year, title and graph-search scope. Current arXiv version is newer; BibTeX uses the original preprint year. |
| pq | https://www.researchgate.net/profile/Herve-Jegou/publication/47815472_Product_Quantization_for_Nearest_Neighbor_Search/links/00b4953c9a4b399203000000/Product-Quantization-for-Nearest-Neighbor-Search.pdf | Original author manuscript, not a secondary summary: first pages verify Cartesian subspaces, authors, publication in TPAMI 33(1):117–128 (2011) and DOI. HAL and DOI direct access failed; author-manuscript mirror succeeded. |
| SIGMOD 2027 | https://2027.sigmod.org/calls_papers_sigmod_research.shtml | Official CFP: 12 content pages, references unlimited, separate limited appendix, anonymous ACM sigconf two-column Letter PDF <=10 MB; PACMMOD reserved for accepted-paper production. |

BibTeX records intentionally identify preprints when those are the versions verified. Preprint years must not be silently relabeled as conference years. Replace with final publisher metadata only after checking it. This is a focused related-work check, not an exhaustive novelty search across all disk ANN literature.

The manuscript's symmetric-score identity and local margin argument are direct algebraic derivations from the implementation, not attributed to a theorem in these papers. Floating-point guards, randomized Hadamard versus fully random orthogonal transforms, INT8 saturation, and adaptive graph selection prevent automatic transplantation of a base quantizer's probabilistic claims.
