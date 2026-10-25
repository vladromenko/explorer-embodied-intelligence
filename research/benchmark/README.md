# Evidence-based evaluation helpers

`evaluate.py` provides deterministic, executable helpers for frozen protocol hashes, episode/session split validation, independent trial accounting, Wilson intervals, retention and symbolic skill composition. Symbolic composition returns skill IDs; it has no robot control API and never authorizes motion.

```sh
python research/benchmark/evaluate.py /path/to/frozen-protocol.json /path/to/trials.jsonl --output /path/to/report.json
python -m pytest research/benchmark/tests -q
python scripts/check_provenance.py
```

The report preserves UNKNOWN outcomes with identification bounds, separates simulation/physical/replay evidence, rejects duplicate trials and post-hoc exclusions, and binds each trial to the frozen protocol. Confidence intervals conditional on known outcomes are labeled explicitly; they do not remove missing-outcome uncertainty. Safety stops and interventions are recorded independently.

Retention is missing-aware. It does not invent zero success when a previous task has not been re-evaluated. A composition plan requires matching robot, declared capabilities and validation domain. It is not proof that a physical composition succeeds.
