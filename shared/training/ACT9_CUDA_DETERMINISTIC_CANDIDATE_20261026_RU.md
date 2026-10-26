# ACT9 deterministic CUDA · только fixture candidate

После отрицательного current-default run выполнен отдельный finite candidate
на тех же448 actual CAD teacher RGB+preceding command9 train frames, policy
factory/optimizer/source graph/runtime и shape64/batch2/chunk4FP32. Только
fixture процесс явно установил `CUBLAS_WORKSPACE_CONFIG=:4096:8`,
`torch.use_deterministic_algorithms(True)`, `cudnn.deterministic=True`,
`cudnn.benchmark=False`. Constraints включены в resume_context/signature.
Настройки production helpers/runtime этим запуском не изменялись.

Фактически1test passed42.73s, unit46.174s. Uninterrupted8 и4→durable exit75→
новыйPID с намеренно другими initial weights→4 совпали побитово: typed tensors
model, optimizer, losses, sampler, Python/NumPy/TorchCPU/allCUDA RNG. Final weights
SHA681d792e7046b0708495e41c520b73fd4fac6b94912dafc98c75d59c8fbcc8e2.
Kernel cgroup peak2,838,171,648B, max/oom0, own4GiB/swap0/CPU200%.
Pause pointer+checkpoint отдельно скопированы до restart для дальнейшего аудита.

Это доказательство только explicit fixture settings. Production profile должен
отдельно гарантировать эти constraints до CUDA startup и заново пройти actual
current-source optimizer/save/reload и pause/restart qualifier. Его readiness
здесь false. Отрицательное свидетельство для прежних defaults сохранено.
Другие action/state shapes, precision, devices и physical skills не допущены.

Основания настройки: [PyTorch2.12 reproducibility](https://docs.pytorch.org/docs/2.12/notes/randomness.html)
и [NVIDIA cuBLAS reproducibility](https://docs.nvidia.com/cuda/cublas/#results-reproducibility).
Никакие controller, servo, power или hardware параметры не менялись.
