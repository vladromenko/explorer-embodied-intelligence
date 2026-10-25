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

## Integrating reviewed batches

After merging per-paper review files, run `python research/literature/tools/registry.py --ingest-reviews --export-csv > research/literature/REVIEW_STATUS.json`. Ingestion validates every source hash and explicit review boolean before atomically changing earned statuses. An invalid review leaves the registry unchanged. Retrieval never creates review credit. The checker verifies accounting consistency, not the semantic quality or completeness of a human/agent reading.

## Completed review acceptance

The validated registry contains 102 unique substantive fulltext reviews, including 35 technical deep reviews, bound to 130 retrieved primary PDFs. Original candidate seed 129 remains preserved; the expanded catalog has 135 candidates / 134 unique identities, with P016 explicitly excluded as the P015 duplicate. QUALITY_AUDIT.json verifies every reviewed PDF byte hash and all 677 page locators; these mechanical checks do not certify semantic review quality.

Original-source code inspection flags total 3 (ACT, Diffusion Policy, UniVLA); original-author-repository execution remains 0. Actual maintained SB3 PPO and official LeRobot ACT executions, configuration changes and checkpoint evidence are recorded separately in methods/METHOD_REGISTRY.yaml and experiments/. Code/model/data licenses and physical qualification remain separate fields.
