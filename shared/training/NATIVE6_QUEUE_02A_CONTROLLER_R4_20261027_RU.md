# R4: общий CPU-исполнитель после интеграции записи02A

Новый frozen source: `eda1fc4d786145b7486025313a145aabca06c8f0`.
Изменения импорта approved native02 и retry queue изменили полные SHA
`job_runner.py`/`store.py`; прежний R3B не объявляется доказательством новой ревизии.
Все старые R3B/R2/PROFILE4 originals сохранены.

Реально выполнены два существующих официальных ACT6/DP6 теста:
Supervisor→bootstrap→job_runner→pause2→exit→новый PID→ещё6, всего8 updates.
**2 PASS /119.24s**. Model/optimizer/losses/sampler/Torch CPU RNG exact;
собственный Python/NumPy RNG продолжает paused checkpoint. Typed tensor
сравнение отдельно проверило dtype/layout/shape/values. Итоговые веса обоих
backend совпали с соответствующими историческими PROFILE4.

Unit `explorer-02A-controller-20261027-eda1fc4-b.service`, invocation
`c4a57277f9144137bfea5b3d27ac8154`; CPU2/4GiB/swap0, peak3450413056B,
все memory.events0. Unit завершён/reaped. В обоих временных Store nodes=[],
production state не записывался. Исходный Station SHA не изменён.

Опубликованы отдельные immutable PREFLIGHT/RUNTIME/ACT_QUALIFICATION/
DIFFUSION_QUALIFICATION/SOURCE_LIBRARY/SOURCE_TREE/SUMMARY с prefix
`NATIVE6_QUEUE_02A_CONTROLLER_R4_20261027`. Проверены оба реально созданных
source.tar, все извлечённые regular bytes, четыре actual worker jobs,
checkpoints/processor hashes и старый→новый PID/start_ticks/boot.

Приватные originals:
`/mnt/explorer-data/explorer-work/experiment02-controller-20261027-eda1fc4/result-b`.
Первый setup launch с неверным полным SHA отвергнут guard до модели; журнал
`preflight-wrong-full-sha-attempt-a.log` сохранён отдельно. Никакое выполнение
модели или успех задачи из него не выводится.

Это общий executor/native6 proof. Он не подменяет target9/fused106, официальный
LeRobotDataset round-trip, independent offline/live-shadow02B и реальные
показы ROSMASTER. Физический task success не проверялся и не заявляется.
