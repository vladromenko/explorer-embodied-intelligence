# Explorer: проверка, воспроизведение среды и резервные копии

Это инструкция для уже настроенных Homebrain и Raspberry Pi. Nano, его ROS-контроллер, прошивка, параметры движения и калибровки сохраняются. Команды ниже не выполняют движения. Новые утилиты пока запускаются отдельно от `explorerctl`.

## Что действительно установлено

Снимки собраны 26 октября 2026 года чтением metadata и `pip freeze --all`; Torch/CUDA и моторные библиотеки при сборе не импортировались. Прямые URL, учётные данные и editable-пути из freeze удалены; вместо них сохраняются установленные версии. Для Torch/torchvision дополнительно записаны статические `version.py`, его SHA256 и SHA256 установленного `RECORD`.

| Назначение | Интерпретатор | Проверенные версии |
| --- | --- | --- |
| Homebrain, сервисы и CPU-обучение | `~/.local/share/explorer/venv/bin/python`, Python 3.12.3 | Torch metadata 2.11.0, build **2.11.0+cu130**; torchvision 0.26.0+cu130; LeRobot 0.6.1; MuJoCo 3.17.0; SB3 2.9.0; NumPy 2.2.6 |
| Homebrain, проверенный ACT на GPU | `~/.local/share/explorer/gpu-venv/bin/python`, Python 3.12.3 | **torch 2.12.0+cu132**, torchvision 0.27.0+cu132, NumPy 2.2.6; остальные пакеты читаются также из основной среды через `.pth` |
| Pi, камера/Hailo/станция | `~/.local/share/explorer/servo-venv/bin/python`, Python 3.13.5 | torch 2.11.0+cpu, LeRobot 0.6.1, Feetech SDK 1.0.0, pyserial 3.5, системный OpenCV 4.10.0, NumPy 2.2.4, HailoRT 5.1.1 |

Полные наблюдаемые версии, ОС, оборудование, пути сред и хеши:

- [Homebrain CPU](environments/homebrain-20261026.json).
- [Homebrain GPU](environments/homebrain-gpu-20261026.json).
- [Raspberry Pi](environments/pi-20261026.json).

Homebrain: Orin NX, Ubuntu 24.04.4, L4T R39.2.1. Pi: Raspberry Pi 5, Debian 13, среда создана с `--system-site-packages`, поэтому использует системные Hailo/OpenCV. Freeze Pi включает Debian-пакеты и не является списком для установки через pip. Наблюдаемый freeze Homebrain также содержит остаточные `nvpl-blas`, `nvpl-lapack`, `nvidia-cudss-cu13` после отвергнутого эксперимента; они не назначаются как зависимости проверенного ACT.

## Проверка без изменения состояния устройств

На Homebrain:

```sh
EXPLORER_RELEASE="$HOME/.local/share/explorer/current"
EXPLORER_CPU_PY="$HOME/.local/share/explorer/venv/bin/python"
PYTHONDONTWRITEBYTECODE=1 "$EXPLORER_CPU_PY" "$EXPLORER_RELEASE/scripts/doctor.py" --node homebrain
```

На Pi:

```sh
EXPLORER_RELEASE="$HOME/.local/share/explorer/current"
EXPLORER_PI_PY="$HOME/.local/share/explorer/servo-venv/bin/python"
PYTHONDONTWRITEBYTECODE=1 "$EXPLORER_PI_PY" "$EXPLORER_RELEASE/scripts/doctor.py" --node pi
```

Doctor читает состояние существующих user-сервисов, разрешения конфигурации, свободное место, локальную БД/очередь и `GET /inventory` уже работающего Pi. Он не открывает камеру или servo-bus, не перезапускает сервисы и не обращается к Nano. Для проверки только локальных файлов есть `--no-http`.

Готовность обратной связи рук определяется двумя конечными векторами по шесть реально измеренных координат, возрастом измерения и общим boot clock. Оригинальные SHA256 двух калибровок сравниваются с `_registry/so101-calibration-manifest.json`. Одного флага capability недостаточно. Порог возраста используется только в отчёте; настройки устройств он не меняет. Свежие координаты и изображение не означают успешного выполнения физического навыка.

Очередь Pi проверяется локально по недоставленным событиям; если она отсутствует на Homebrain, выводится неизвестное значение, а не ноль. Для очередей свыше 1000 записей возраст рассчитывается по первым 1000 и явно помечается как ограниченная выборка. Хеши старого контроллера по умолчанию имеют `checked=false`: при наличии явно выбранной локальной копии папки с шестью исходными JSON можно передать `--controller-dir ПАПКА`. Это только чтение файлов и сравнение с `_registry/controller-config-baseline.json`.

## Воспроизведение среды

Существующие `venv`, `gpu-venv`, `servo-venv`, system Hailo 5.1.1, `/usr/local/libexec/milo-hailo-recovery.py`, его root timer, SSH/Wi-Fi и кэш калибровок сохраняются. Перепрошивка, новая калибровка и общий `apt upgrade` для этой процедуры не нужны. Во время записи или обучения пакеты не меняются.

Для нового развёртывания используется отдельный каталог среды, соответствующая версия Python и сохранённый исходный release. Основная среда сохраняет Torch 2.11.0/torchvision 0.26.0 и запускает CPU-задания с `device=cpu`. Две библиотеки GPU ставятся только в отдельную среду из официального индекса `https://download.pytorch.org/whl/cu132` с точными версиями `torch==2.12.0+cu132` и `torchvision==0.27.0+cu132`; её `.pth` указывает на согласованную основную среду. NumPy фиксируется на 2.2.6. Эти две версии выходят за декларируемые верхние границы LeRobot 0.6.1; это локально проверенное исключение, а не универсальная совместимость.

Снимки freeze служат проверкой состава; их нельзя вслепую устанавливать в работающую среду или на другую ОС. На Pi новая среда использует системные пакеты и CPU-wheel Torch, без подмены системных Hailo/OpenCV. Дополнительные сторонние Jetson AI wheels из отвергнутого эксперимента не используются. Для восстановления пустого диска отдельно нужны копии исходников/venv либо заново подготовленный и проверенный набор wheel/OS-пакетов; metadata backup ниже их не содержит.

После сборки новой GPU-среды отдельный тест можно запустить явно, выделив **новую** папку результатов:

```sh
PYTHONDONTWRITEBYTECODE=1 "$HOME/.local/share/explorer/gpu-venv/bin/python" \
  "$HOME/.local/share/explorer/current/scripts/qualify_gpu.py" \
  --root "$HOME/.local/share/explorer/current" \
  --output "$HOME/.local/share/explorer/state/qualification/act-new-build-check"
```

Он выполняет вычисления и сохраняет тестовый checkpoint, но не обращается к роботам. Существующее свидетельство [ACT FP32](../_registry/evidence/act-cu132-fp32-20261026.json) фиксирует конечные loss, изменение весов, save/reload, TF32 off и расхождение CPU/GPU около `9.54e-7`. В [реальном задании обучения](../_registry/evidence/station-gpu-training-job.json) были 480 обновлений по записанным данным. Предупреждение wheel о SM87 сохраняется: подтверждён этот ACT-путь, не произвольная CUDA-нагрузка и не выполнение физической задачи.

Повторный безопасный снимок версии выбранной среды:

```sh
PYTHONDONTWRITEBYTECODE=1 "$HOME/.local/share/explorer/gpu-venv/bin/python" \
  "$HOME/.local/share/explorer/current/scripts/environment_inventory.py"
```

Он не выполняет GPU/Hailo/моторные проверки. Doctor, запущенный из GPU-среды с `--no-http`, сопоставляет записанные qualification-версии с этой средой. Совпадение версий само по себе не доказывает идентичность wheel: поле `same_build_identity_proven` честно остаётся false.

## Частная резервная копия metadata

На Homebrain сначала создаётся закрытая родительская папка; выбранная папка самой копии должна быть новой:

```sh
install -d -m 700 "$HOME/.local/state/explorer/backups"
PYTHONDONTWRITEBYTECODE=1 "$HOME/.local/share/explorer/venv/bin/python" \
  "$HOME/.local/share/explorer/current/scripts/backup_state.py" \
  --destination "$HOME/.local/state/explorer/backups/metadata-20261026"
```

По умолчанию выбирается `data_dir` из используемого `~/.config/explorer/config.json`, включая SSD. Явный `--state` переопределяет его. SQLite копируется через backup API с учётом committed WAL; обычное копирование одного живого `.sqlite3` не используется. У копии каталог имеет режим 700, файлы — 600. Она содержит БД, локальные `config.json/node.env/arms.json/access-code.txt`, найденные JSON калибровок, хеши исходников и состав среды. Токены и пароли не печатаются, но сама копия содержит частную конфигурацию и хранится закрыто.

Калибровки Pi и его `arms.json` находятся на Pi. Центральный запуск автоматически их не получает: в receipt видны `calibration_files` и `calibration_source_present`. Для полного набора metadata сначала отдельно сохраните частную копию конфигурации и оригинальных калибровок Pi; её каталог можно явно передать `--calibration`. Наличие только центрального архива не означает, что удалённые файлы сохранены.

Перед успешным завершением утилита проверяет SHA256 всех членов и реально восстанавливает их во временную папку, открывает **новый изолированный Store**, сравнивает количества записей и хеши частных файлов. В receipt есть `test_restore_passed`, число файлов, размер и SHA256 manifest. SQLite snapshot ограничен 60 секундами; запросы проверки имеют собственный лимит времени.

Эта копия **не включает** записи камер, датасеты, checkpoint/model payloads, CAD-меши, исходный checkout и виртуальные среды. Для восстановления всего диска дополнительно нужна их отдельная копия на другом носителе; хеши исходников не заменяют сами исходники. Архив на том же SSD защищает от ошибочного изменения, но не от потери SSD.

## Проверка и репетиция восстановления

Проверка по умолчанию ничего не восстанавливает:

```sh
PYTHONDONTWRITEBYTECODE=1 "$HOME/.local/share/explorer/venv/bin/python" \
  "$HOME/.local/share/explorer/current/scripts/restore_state.py" \
  --backup "$HOME/.local/state/explorer/backups/metadata-20261026"
```

Для реальной репетиции нужна явно выбранная новая папка вне live state/config/calibration, checkout, старого контроллера и самого backup. Все защищённые пути разрешаются с учётом symlink, включая калибровки на SSD:

```sh
install -d -m 700 "$HOME/.local/state/explorer/rehearsals"
PYTHONDONTWRITEBYTECODE=1 "$HOME/.local/share/explorer/venv/bin/python" \
  "$HOME/.local/share/explorer/current/scripts/restore_state.py" \
  --backup "$HOME/.local/state/explorer/backups/metadata-20261026" \
  --destination "$HOME/.local/state/explorer/rehearsals/check-20261026" --apply
```

Внутри появляются `data/` и `private/`, затем проверяются хеши и открытие Store. Работающие сервисы, symlink `current`, реальная конфигурация, калибровки и контроллер не переключаются. Это репетиция; автоматического перезаписывания live state здесь нет.
