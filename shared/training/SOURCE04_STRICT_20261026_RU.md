# Квалификация строгого native6 admission и ACT shadow loader

Зафиксирован `d4b02cf` вместе с metadata-only Station finish fence2840708.
ACT6 и Diffusion6 используют один server/cohort/Spec/source/split verifier.
Прежние qualification файлы не изменены; ACTIVE pointer переведён на новый
`DIFFUSION_NATIVE6_20261026_CURRENT_STRICT_*` набор.

ACT: настоящий официальный Dataset4episodes/32frames из5 synthethic originals
(3train/1development/1sealed без sealed model payload),8CPU updates,
loss1.107820→.293283, model reload0, actual saved official processor comparison0.
Сохранённый ACT действительно загружен native6 shadow adapter и выполнен на
отдельном original development RGB+measured-state software observation.
Receipt честно называется offline inference; SIM fixture нельзя загрузить для
live physical shadow без явного software-only режима. Тест1pass14.48s;
RSS1,181,634,560B, cgroup peak1,245,806,592B.

Diffusion: тот же scope оригиналов, официальные4updates, искусственное
прерывание после durable checkpoint, оставшиеся4updates после reload;
loss1.290305→.689718, model reload0, RGB conv gradient.0843255.
Тест1pass20.83s; RSS1,241,292,800B, cgroup peak1,392,971,776B.
Оба собственных transient user unit имели MemoryMax4GiB, swap0 и CPUQuota200%;
memory max/oom counters0. Systemd's displayed accounting differs от process RSS
и kernel memory.peak; здесь сохранены сами измеренные значения.

В обоих случаях точные package RECORD/location/version, official sources и
repository source/rule SHA совпали до/после выполнения. DP resume metadata
извлечены checksum-проверенным torch.load(weights_only=True), canonical signature
`9e90fdc7bbffd25108a2fc1b0ec055055755ba3d5adea95e1349b3ded82f73e0` совпала
с pointer/checkpoint/result. Root runtime selector действительно принял новые
proofs: binding `01e8def67cf0f9773640e76ccaddd49509347ec2b333e48d9965941d91c592d0`.

Сравнение с прежними фактическими qualification показало **точное совпадение
model.safetensors SHA** для ACT и DP: admission/save-metadata дополнения не
изменили optimizer math/settings/normalization и получившиеся веса.
Новые official processor/source metadata файлы сохраняются отдельно.

Все исходные артефакты выполнения:
`/mnt/explorer-data/explorer-work/qualification-source04-strict-20261026`.
Квалификации не используют Hub, simulator, camera, serial или аппаратные APIs;
production-state не менялся. Веса/MAE/source labels не подтверждают предметный
навык, physical success или deployment readiness. Public router→worker smoke
остаётся отдельной проверкой root; queue integration не приписана этому тесту.
