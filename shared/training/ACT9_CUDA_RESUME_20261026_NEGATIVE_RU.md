# Отрицательная CUDA bit-exact resume проверка

Сохранён фактический failed run, без ослабления допуска. Immutableb72cd0b,
same qualified official ACT9 image64/batch2/chunk4 FP32 runtime/source graph.
Уже записанная успешная CAD/contact scripted train episode448frames, RGB и
preceding command9 через настоящий RecordedChunks. Никакой новой симуляции,
camera/device owner или hardware IO.

Reference8 и отдельный4→exit75→новыйPID4 реально завершили optimizer; RNG,
sampler, optimizer count/source signature восстановлены. Строгий test упал
на tensor equality:1failed49.40s. Flag CUDA_bit_exact_resume_qualified=false.
Memory peak3,168,530,432B; max/oom events0. Все процессы завершились.

CPU-only forensic readback фактических checkpoint4 и8 показал:

- Уже ДО pause обе независимо инициализированные одинаковым seed ветки имеют
  разные93/186 model tensors, max absolute difference4.4877953e-7.
- Initial model digest одинаков. Все RNG и sampler state точно одинаковы
  как после4, так и после8. Первый loss одинаков, step3 loss отличается
  на1.79e-7. Это наблюдаемая численная недетерминированность prefix, а не
  свидетельство неправильного RNG restore.
- После8 max model difference8.7196007e-5. Результат не переименован в pass
  по приблизительному сравнению; CUDA exact parity остаётся неподтверждённой.

Actual runtime metadata: deterministic_algorithms=false,
cudnn_deterministic=false, cudnn_benchmark=false, TF32off. PyTorch описывает
дополнительные ограничения и настройки воспроизводимости CUDA в
[официальной документации2.12](https://docs.pytorch.org/docs/2.12/notes/randomness.html).
Отдельная deterministic candidate требует фактической новой проверки и
явного источника настроек; этот failed run не разрешает менять production
model math, исходный контроллер или присваивать другой profile готовность.

Index `_registry/evidence/act9-cuda-resume-negative-20261026.json` связывает
байтовые archives preflight/runtime/reference/resumed/pause receipt и CPU audit.
Оригинальные checkpoints/logs/source archive сохранены вне production-state
в `/mnt/explorer-data/explorer-work/qualification-official-act9-cuda-process-resume-20261026`.
