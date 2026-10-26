# Явный ACT9 CUDA process profile

Новый source contract, пока требующий fresh actual qualification:
`cuda_execution_profile="act9_cuda64_b2_c4_fp32_deterministic_v1"`,
devicecuda, state9/action9, image64/batch2/chunk4, actualFP32.
CPU, native6 и available-sensor state106 этим profile не подменяются.
Анонимные или другие CUDA shapes отклоняются с явной причиной. UI/server
могут подставлять ID только в действительно соответствующий typed request.

Scheduler формирует environment ДО Popen/library startup:
`CUBLAS_WORKSPACE_CONFIG=:4096:8`, `NVIDIA_TF32_OVERRIDE=0`,
`TORCH_ALLOW_TF32_CUBLAS_OVERRIDE=0`. Factory проверяет env и actualdefaultFP32,
до CUDA startup включает strict deterministic algorithms/cudnn, отключает
benchmark/TF32 и выбирает highest matmul precision. Это настройки конкретного
изолированного profile процесса; CPU factory не меняет глобальные flags.
Model architecture, data normalization, loss и optimizer остаются прежними.

GPU graph4 содержит16 source files, включая lightweight runtime_profiles.py.
Binding обязан совпасть с конкретным profileID/runtimeconstraints и actual
worker receipt, подтверждающим environment и отсутствие CUDA initialization
перед конфигурацией. Resume signature содержит новый helperSHA, environment,
runtimeflags и executionprofile. Старые graph1/2/3 и их исходные immutable
releases/receipts сохраняются; новые defaults на старые jobs не накладываются.

Fixture candidate дал actual bit-exact8vs4+newPID4 при strictdetflags, а
обычные defaults дали сохранённый отрицательный результат. Ни один из этих
старых proof bytes не объявляет этот новый production source квалифицированным.
Нужны fresh optimizer/save/reload, GPU process resume и controller checks после
окончательных hooks, затем отдельная публикация finalrelease binding.
