# Working draft — negative simulation pilots

**NOT SUBMISSION-READY.** Paper work is parked at a safe Git checkpoint after
an explicit user reprioritization to 02 → 03 → 01 → 04 → 05 → 06. The overall
Explorer task remains active. No new paper computations are scheduled.

`main.pdf` is an actual six-page TeX/BibTeX compilation of the current
`main.tex`: five content pages and one reference page. It was produced on
Homebrain by pinned user-local ARM64 Tectonic0.17.0, without system-package or
Explorer-venv changes. `BUILD_NEGATIVE_PILOTS_CHECKPOINT.json` binds the exact
TeX, bibliography, unchanged official IEEEtran class, included figures,
compiler, PDF and logs. There are no undefined citations or overfull boxes;
all PDF font programs are embedded Type1/Type1C. This is an automated structural
check, not an official venue-compliance certificate or completed visual review.

The current draft adds actual negative whole-object pilots B (0/8 complete)
and C-v2 (0/16 complete), retaining partial effects and the separate C-v1
UNKNOWN1/NOT_STARTED15 disposition. It also reports 1756 TRAIN-only prior-zero
queries on four unchanged models. Labels and phase stay in the scorer;
TRAIN errors are not closed-loop gains, ABS/INCR normalized errors cannot be
ranked across representations, and no unique failure cause is established.
No physical result, learned-source04/H2, H1–H5 finding or master completion
is claimed. The central acceptance-treatment comparison remains untested.

The exact public input JSONs and their SHA are in
`figures/negative_learning_20261027/PROVENANCE.json` and the build receipt.
`negative_counts_table.tex` and `train_fit_table.tex` are generated from those
inputs, rather than manually transcribed counts. Standard Matplotlib exports
include CSV, SVG, PNG and font-bearing source PDFs. Separate Ghostscript
outlined-vector derivatives are used in the manuscript to avoid TrueType/Type3
figure fonts; the original contact PDF remains unchanged. Outlined labels have
limited PDF text search/accessibility; source SVG/CSV and original PDFs remain.
Both new figure source/outlined PDFs repeated byte-identically in the same
pinned environment. The PNGs were inspected; the complete six-page manuscript
has not yet been visually reviewed.

## Preserved build history and remaining checks

- `BUILD_VERIFICATION.json` is the unchanged historical three-page Mac build,
  tied to its old source SHA. It does not describe the current manuscript.
- `BUILD_CONTACT_V10_FIGURE_VERIFICATION.json` remains the unchanged earlier
  standalone V10-figure receipt. Its statement that the then-current full
  manuscript was uncompiled is historical.
- Private attempt00 compiled the old four-page source with real TeX.
  Attempt01 retained an offline missing-`cmr8.pfb` failure. Attempt02 completed
  the current six-page source after fetching that font into the separate cache.
  Logs and source snapshots are retained under
  `/mnt/explorer-data/explorer-work/paper-negative-pilots-20261027/attempts`.
- The new offline two-build driver is prepared and has eight source-only guard
  tests, but its complete repeated-build path has **not** passed end to end.
  Full-paper duplicate-byte proof and full visual review are **NOT RUN**, because
  the user parked further paper work after the running atomic build completed.
  No alternate PDF generator is substituted for compiled TeX.

## Reproduction tools when this stream resumes

`scripts/plot_negative_learning_paper.py --output FRESH_DIRECTORY` checks the
three public JSON digests, preserves every matched outcome and generates the
figures/tables. It never executes models, simulation or fitting.

`scripts/build_research_paper.py prepare-tool` downloads only the pinned
[official Tectonic0.17.0 ARM64 Linux release](https://github.com/tectonic-typesetting/tectonic/releases/tag/tectonic%400.17.0)
into a user-local cache and verifies archive and executable SHA. `build --output
FRESH_DIRECTORY` requires a CPU≤2/MemoryMax≤2GiB/swap0 unit and an already populated
private TeX cache. It snapshots inputs, uses `--untrusted --only-cached`, fixed
`SOURCE_DATE_EPOCH=1793059200`, deterministic mode and separate source/output
roots. It rejects unresolved citations, overfull boxes, author/path leakage,
nonembedded/non-Type1 font programs and different duplicate PDFs. Its unqualified
end-to-end status above must remain visible until actually exercised.

RSS2027 remains a working venue inferred from the recalled deadline, not a
separately confirmed venue choice. The [official CFP](https://roboticsconference.org/information/cfp/)
was rechecked on 2026-10-27: up to five content pages plus one reference page,
anonymized metadata, no extra draft markings on pages and the supplied format.
No font size, page dimensions or line spacing in the official class is changed.
The current draft removes external source-release hyperlinks from the blinded
bibliography while preserving the cited revision and the full internal corpus.
Authors must still decide the contribution, novelty, scientific sufficiency,
responsibility, final venue compliance and submission. No submission occurred.
