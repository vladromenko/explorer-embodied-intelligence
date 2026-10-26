# Проверка текущего общего ACT на Orin

Настоящий запуск использовал frozen source `010ba3e04fc727cd37264da1fff467aa69fb9ac7`,
официальный LeRobot0.6.1 и существующий GPUvenv. Все14 исходников (включая
shared ACT optimizer/artifacts и реальные pilot/dataset adapters) и runtime
package METADATA/RECORD/.pth совпали до и после выполнения. Дополнительно
измерены фактические SHA официальных ACT config/model/processor/factory.
Прежние receipts и binding релиза8e30 сохранены без изменений.

Отдельный read-only audit retained step0/step8 checkpoints загрузил только
checksum-проверенный `torch.load(weights_only=True)`, пересчитал resume metadata
signature и обнаружил ненулевое изменение реальных visual backbone conv weights
(первая conv maxabs.000752866). Initial checkpoint SHA измерен после выполнения;
это явно обозначено и не выдаётся за независимую временную аттестацию.

12 тестов прошли за75.50s. Настоящая CAD/MuJoCo демонстрация выполнила полный
контактный сценарий; из неё обучен RGB+preceding_command9 ACT:8 updates,
loss.992425→.313531, save/reload max error0. Официальные сохранённые processors
исполнены. Независимый32step development rollout0/1; неуспех сохранён вместе
с exact native integration-state replay. Это не доказательство предметного
навыка, контакта в реальном мире или успешного обучения при большем бюджете.

Собственный unit: MemoryMax4GiB, swap0, CPUQuota200%, RuntimeMax240s. Измерены
RSS3,808,825,344B и kernel cgroup peak4,059,635,712B; max/oom counters0.
PyTorch allocator peak296,047,616B (reserved327,155,712B). Эти счётчики имеют
разный охват: cgroup не изолирует все Jetson unified GPU allocations.
Wheel предупреждает, что OrinCC8.7 исключён из объявленной поддержки;
узкий реальный проход не подтверждает общую совместимость wheel/toolchain.

`qualification`, `preflight`, `result`, `binding` JSON здесь — точные копии
из `/mnt/explorer-data/explorer-work/qualification-act9-shared-20261026`;
хеши и scope перечислены в `_registry/evidence/gpu9-shared-current-20261026.json`.
`ORIGINAL_BINDING` сохраняет первый результат публикации. Отдельное усиление
admission helper повторно проверило неизменный actual receipt/runtime и добавило
в binding точные официальные source bytes; новый `BINDING` получен без retraining.
При изменении actual library файла даже с прежним RECORD допуск теперь отзывается.
Реестр bindings пока изолирован в том каталоге. Root может явно опубликовать
тот же actual receipt для конечного commit-addressed release только при
совпадении всех14 measured source SHA. Текущая версия helper умеет graphv2;
legacy releases без sharedACT остаются graphv1, их bytes не переписываются.

Проверен только job `manipulation_student`, state9/action9,64px/chunk4/batch2.
Нельзя переносить допуск на fused106D ROSMASTER, native6 source04, Diffusion,
общую CUDA factory, иной dtype или профиль. Отдельный process-restart CUDA
optimizer resume здесь не проверялся. Аппаратура/Hub/control APIs не вызваны;
production-state не менялся, ни один привод не двигался.
