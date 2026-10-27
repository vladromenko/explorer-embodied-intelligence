# R5: общий CPU-исполнитель с отдельной DEV-проверкой02B

Frozen source: `63ba28dc7aa4f02e41aa11c48d9d13ad272d6f05`.
Новые scheduler/queue/job_runner hooks изменили полные SHA. Старые R4/R3B/R2/
PROFILE4 originals сохранены; актуальные source hashes не исключались из проверки.

Реально выполнены два прежних официальных ACT6/DP6 теста через
Supervisor→bootstrap→job_runner: pause2→exit→новый PID→ещё6 updates.
**2PASS/113.36s**. Model/optimizer/losses/sampler/Torch CPU RNG exact,
Python/NumPy checkpoint continuation проверен; итоговые веса совпали с PROFILE4.

Unit `explorer-02B-controller-20261027-63ba28d.service`, invocation
`ce246842ac5541e1b49feffd98dd93d3`: CPU2/4GiB/swap0, kernel peak3422298112B,
все memory.events0. Unit завершён/reaped; private Store nodes=[], production
state не использовался, Station SHA сохранён. Actual source archives/extracted
files,4worker jobs/checkpoints и разные PID/start_ticks проверены publisher.

Originals: `/mnt/explorer-data/explorer-work/experiment02B-controller-20261027-63ba28d/`.
Этот proof относится к общему executor/native6. Он не заменяет отдельное
обучение106/LeRobotDataset, реальное live-shadow или физическую приёмку02.
