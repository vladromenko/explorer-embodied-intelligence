# Действующие решения

- Один private Git monorepo, canonical checkout на Homebrain; immutable releases
  и отдельные worktree. Root единолично интегрирует, меняет общие контракты и
  развёртывает production. Нет зависимости runtime от Mac/чата.
- Единственная идентичность экспериментов —
  [experiments.py](shared/explorer_shared/experiments.py):01 autonomous,
  02 keyboard/gamepad ROSMASTER,03 SO101 leader→ROSMASTER,04 SO101 source,
  05 handover,06 source04→ROSMASTER. Историческая нумерация сохраняется через
  проверенные projections, без переписи raw evidence.
  [Подробности](research/audits/EXPERIMENT_NUMBERING_V2_RU.md).
- ROSMASTER использует существующие ManualArm/Ruckig/publisher; Pi — исходный
  Station/servo loop. Новые permissions/interlocks проверяют права у этих же
  границ. Native9 и native6, estimated и measured, clocks узлов различаются.
- Scope остановки — точный владелец/epoch; потеря наблюдения не возобновляет
  прошлый grant. Double-hold latch переживает STOP, повреждение/утрату WAL и
  restart; снимается только отдельным допустимым подтверждением.
- Shared durable queue сериализует тяжёлые ресурсы; модели/optimizer/RNG/sampler
  и source/runtime binding сохраняются. Новые release не меняют pinned старые jobs.
- Scientific export копирует выбранные проверенные bytes и явно сообщает PARTIAL.
  Не выполняет произвольный архивный код и не экспортирует auth/raw SQLite.
  Source train не задаёт target train; новый target split требуется явно.
- SIM/contact diagnostics, learned outcome, physical qualification и scientific
  novelty — разные claims. Все стартовавшие попытки, ошибки и setup failures
  сохраняются. Изменение решения оформляется конкретным evidence/commit.
