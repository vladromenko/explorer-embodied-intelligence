#!/usr/bin/env python3
"""Safe CPU-only CLI wrapper for the same raw validation/export backend as UI."""
import argparse
import json
from pathlib import Path

from so101_lab.lerobot_export import export_dataset, validate_raw_dataset
from so101_lab.resources import ROOT


def main():
    parser = argparse.ArgumentParser(description="Экспорт raw SO-101 в официальный LeRobot 0.6.1 Dataset, без hardware/GPU")
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--allow-test-data", action="store_true", help="Только явная техническая проверка, не реальные демонстрации")
    parser.add_argument("--fps", type=int, help="Явно известный nominal FPS; не запрос пересэмплирования")
    parser.add_argument("--report", type=Path)
    arguments = parser.parse_args()
    if arguments.validate_only:
        result = validate_raw_dataset(arguments.dataset, allow_test_data=arguments.allow_test_data, fps=arguments.fps)
    else:
        options = {"allow_test_data": arguments.allow_test_data}
        if arguments.fps is not None:
            options["fps"] = arguments.fps
        result = export_dataset(arguments.dataset, arguments.output, options)
    if arguments.report:
        report = arguments.report.resolve()
        if not report.is_relative_to(ROOT):
            raise ValueError("Report должен находиться внутри независимой лаборатории")
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
