# Текущая native6 квалификация после canonical04 миграции

Квалификация выполнена на зафиксированном e9df048d3a8099fef2e4d78c1b2e63f9de81ce2a:
canonical registry с новым каталогом04 и последний ENGINEERING_GLUE consumer
header. Прежние receipts не менялись. Их source mismatch был корректным отказом,
а не поводом обойти checksum.

`DIFFUSION_NATIVE6_ACTIVE_BINDING.json` указывает только на новые датированные
SOURCE_BINDING/QUALIFICATION/IMPLEMENTATION_BINDING файлы. Runtime observation
содержит точные package location/version/RECORD и официальные policy/config/
optimizer/DDIM/DDPM SHA ДО настоящего запуска и ПОСЛЕ него; они совпали.
В отличие от старого checkpoint, текущая квалификация действительно измерила
config/optimizer sources и package RECORDs при выполнении native6 задачи.
Resume metadata извлечены через checksum-проверенный torch.load(weights_only),
его canonical JSON SHA совпадает с checkpoint/pointer/result signature.

Источник —5 принятых синтетических whole-task originals, officialDataset
4эпизода/32кадра, отдельный sealed reference без payload. Настоящий Diffusion6
выполнил4updates, был прерван после durable checkpoint и сделал оставшиеся4.
Save/reload error0, source project/spec owner04, marker2, карточка SIMULATION_ONLY,
native-coordinate loader и исходные SHA проверены. Loss1.290305→.689718;
development chunk error не является успехом предметной задачи.

Тест1pass21.49s, весь собственный unit26.559s; MemoryMax4GiB, swap0, CPUQuota2.
Peak RSS1 327 288 320B, kernel cgroup peak1 409 556 480B; counters max/oom0.
Все артефакты выполнения находятся вне production-state:
`/mnt/explorer-data/explorer-work/qualification-dp6-current-20261026`.
Root qualified_diffusion_runtime(release=...) успешно проверил новый набор,
binding SHA b07cbb4ee422f3964974e0e23dc36e1706b93a1cacabe829025828330e03bb1b.

Публичная очередь/реальный job_runner ещё проверяются root отдельно:
public_queue_integration_qualified=false. Аппаратное исполнение, simulator,
контроллеры, Hub, камеры и serial не запускались. Квалификация подтверждает
только данный source/runtime/profile и программный путь; физический навык
и общее качество Diffusion не заявляются.
