# Официальный Diffusion: компактная CPU-квалификация

Фактический прогон 2026-10-26 на Homebrain находится в
`DIFFUSION_CPU_QUALIFICATION.json`, исходное свидетельство и веса сохранены на SSD:
`~/.local/share/explorer/state/qualification/policy-backends/20261026-diffusion-cpu`.
Это программный/ресурсный smoke на синтетических данных. Общий backend Diffusion
остаётся UNQUALIFIED, ACT сохраняется по умолчанию. Готовность предметного навыка,
сравнительное преимущество над ACT, physical rollout или deployment не заявляются.

Использованы установленный официальный LeRobot 0.6.1 и diffusers 0.38.0. Первичные
локальные modeling/configuration/optimizer/scheduler файлы связаны SHA в receipt.
Модель — настоящий DiffusionPolicy: ResNet18 без загруженных pretrained weights,
U-Net down_dims32/64, RGB64px, две последовательные наблюдавшиеся позиции/картинки,
девять совместных координат состояния/команды, horizon8, четыре будущих команды,
DDIM4 inference. При RGB32px ResNet18 даёт spatial map1×1: SpatialSoftmax потерял
бы изображение. Поэтому factory требует минимум64px, а тест фактически проверяет
ненулевой gradient первого RGB conv и влияние изменённого RGB при фиксированных
state/noise. Обе проверки прошли, а не только наличие image key в конфигурации.

Синтетические train/development — отдельные полные оригиналы, сцены, сессии,
RGB-содержание и clocks; никакие реальные демонстрации не объявлялись
допущенными. Настоящие нейтральные команды базы остаются в общем9D векторе с
действительными масками. History состоит из t-1,t; target horizon начинается
с t-1, а реально выдаваемые моделью команды — offsets1..4. Нет будущих кадров
в actor, подмены измерений или импорта привода. Original arrays/SHA сохранены.

Официальный get_optimizer_preset Diffusion0.6.1 возвращает Adam, не AdamW:
betas .95/.999, lr1e-4, weight_decay1e-6. Выполнены8 настоящих updates,
11 396 289 параметров, optimizer state сохранён, веса изменились. Функция
forward возвращает loss,None; ACT-specific optimizer здесь не применялся.
Loss .840525→1.044719 стохастичен, прогресс предметной задачи из него не следует.
Official save_pretrained/from_pretrained с фиксированным DDIM noise дал error0.
Независимые software windows: normalized chunk MAE .813914 — диагностическое
число, без физического смысла успеха. RGB first-conv gradient norm .313064;
changed-RGB fixed-state/noise action difference .177440.

Собственный transient user unit ограничен MemoryMax4GiB, SwapMax0, CPUQuota200%,
RuntimeMax180s. Скрипт отказывается выделять модель вне этого собственного cgroup.
Восемь updates заняли3.60s, весь unit17.9s. Peak RSS1 136 304 128B;
kernel cgroup memory.peak1 024 737 280B. Счётчики max/oom/oom_kill равны0.
Восемь DDIM4 forward на heldout windows: median58.93ms, p9561.59ms.
Это не latency стандартного большого U-Net/DDPM100 и не GPU benchmark.

В постоянном отдельном runtime
`~/.local/share/explorer/policy-runtimes/diffusion-0.38.0-cpu/bin/python`
установлены только pinned optional overlay dependencies из
`diffusion-cpu-extra-requirements.txt`. Его .pth ссылается на исходный CPU venv;
туда пакеты не устанавливались/не обновлялись. Runtime binding фиксирует ELF,
prefix, pyvenv.cfg/.pth, версии/расположения/RECORD SHA пакетов и источники.
Изменение базовой среды требует новой проверки, а один ELF SHA не различает
виртуальные среды. Не следует переносить этот receipt на новый GPU/runtime.

Следующие отдельные критерии для общего/06 consumer: shared history-window
dataset поверх того же immutable принятого набора, source/phase/split leakage
проверки, DP-specific durable Adam resume/RNG/sampler, native6 save/loader/offline
evaluation, точный профиль/device binding и новая фактическая квалификация.
Ни камера, ни последовательный порт, ни control API здесь не открывались.

Обычные тесты: `pytest -q tests/test_compact_diffusion_candidate.py` —4pass2.34s.
Отдельный реальный прогон (только в новом собственном bounded user cgroup):
`scripts/qualify_diffusion.py --output <новый каталог на SSD>` через описанный
persistent Python. Повторный запуск не затирает существующий каталог результата.
