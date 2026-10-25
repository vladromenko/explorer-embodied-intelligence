# Literature evidence

The original 129 seed candidates are preserved unchanged in `seed_candidates.json`, including the source document hash. They are not 129 finished reviews. The working registry also includes added sources needed to cover lifelong skill learning and composition.

Run `python research/literature/tools/registry.py --export-csv` to validate records and print honest counts. A full-text review is counted only when its substantive review JSON exists, binds to an actually retrieved source SHA256, has page/section evidence, and explicitly records full-text examination. Abstract-only notes and machine retrieval never gain review credit. Duplicate arXiv versions or repeated descriptions of one paper count once.

Legal primary PDFs and extracted page text live outside Git, by default in `~/Library/Caches/Explorer/literature/Pxxx/`. Retrieval metadata and SHA256 are versioned under `evidence/`. Reproduce retrieval with:

```sh
python research/literature/tools/retrieve_fulltexts.py --workers 2
```

This command requires `pypdf` and a network connection. It does not upload research data or download models. Source access failures stay explicit; there is no paywall bypass. The registry tracks `PRIMARY_METADATA_RETRIEVED` separately from a human-confirmed `PRIMARY_VERIFIED`, and `RETRIEVED` separately from `FULL_TEXT_REVIEWED`.

Each finished review describes problem, contribution, learning algorithm, observations/actions, embodiment/data, experiments, strong baselines, a quantitative result with its page/table or `NOT_EXTRACTED`, limitations, code/license evidence, practical NX/Pi/Nano adaptation and reuse decision. Deep reviews additionally cover algorithm details, integration contracts, licensing evidence, required validation experiments and differences from Explorer.

No result in this directory establishes performance of Explorer. Such a claim needs a registered experiment run and independent outcome evidence.
