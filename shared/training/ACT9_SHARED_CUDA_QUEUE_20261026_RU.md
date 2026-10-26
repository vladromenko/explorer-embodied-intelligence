# Текущий checkpoint-aware ACT9 · узкая CUDA квалификация

Фактический запуск immutable source253f6c6 на Homebrain:12tests passed72.03s,
unit77.135s. Реальная CAD/contact scripted демонстрация успешна1/1; официальный
LeRobotACT выполнил8 updates с RGB и preceding command9. Loss.992425→.313531,
weights changed, saved official processors выполнены, reload max error0.
Независимая32step development оценка0/1, native integration-state replay прошёл.

Graph3 содержит15 hashes, включая общий checkpoint_control.py. Каждый SHA
совпал до/после запуска и с текущим root source. Прежние graph1/2 свидетельства
не изменены; новый index `_registry/evidence/gpu9-checkpoint-current-20261026.json`.
Изолированный bindingb89e7865… создан и реально проверен binder-ом. Production
binding должен отдельно указывать конкретный финальный release с теми же15SHA.

Квалифицирован только FP32 CUDA state9/action9 image64 batch2 chunk4. Source04
native6 и ROSMASTER106 этой проверкой не квалифицированы. Torch2.12cu132
предупреждает, что его compiled architectures не включают OrinCC8.7; факт этого
узкого выполнения не превращает весь runtime в универсально совместимый.
TF32off. PeakRSS3,843,653,632B, kernel cgroup3,882,913,792B,
CUDA allocated296,047,616B/reserved327,155,712B; memory max/oom events0.
Ownunit MemoryMax4GiB/swap0/CPU200%; cgroup не изолирует каждую GPU allocation
в общей памяти Jetson. GPU процесс завершён и ресурс освобождён.

Никакой hardware IO, production-state записи или физический успех не заявлены.
Restart optimizer на CUDA в этом run не выполнялся; его нельзя вывести из CPU
process restart proof. Начальный launcher с неверно переписанным полным commit
path завершился до импорта за31ms; исправленный запуск использовал git archive
с настоящим полным253f6c610b1a77f5103098e61c0684f8064317d6. Raw logs сохранены
в ownunit journal и `/mnt/explorer-data/explorer-work/qualification-act9-checkpoint-cuda-20261026`.
