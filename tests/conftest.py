"""Тесты не должны писать в боевой MLflow лаборатории и её спул.

До этого каждый прогон pytest создавал десятки запусков (resume/model/act...) и ~1 ГБ артефактов в data/mlflow-artifacts,
а таймер replay доставлял их из runtime/mlflow-spool в рабочий интерфейс MLflow.
"""
import os
import tempfile


def pytest_configure(config):
    os.environ.setdefault("SO101_MLFLOW_OFFLINE", "1")
    if "SO101_MLFLOW_SPOOL" not in os.environ:
        os.environ["SO101_MLFLOW_SPOOL"] = tempfile.mkdtemp(prefix="so101-test-spool-")
        config._so101_spool = os.environ["SO101_MLFLOW_SPOOL"]


def pytest_unconfigure(config):
    spool = getattr(config, "_so101_spool", None)
    if spool:
        import shutil
        shutil.rmtree(spool, ignore_errors=True)
