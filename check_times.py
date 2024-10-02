"""Check the frequency of the rebuild loop.

This must be run in a directory that has the ``docsbuild*`` log files.
For example:

.. code-block:: bash

   $ mkdir -p docsbuild-logs
   $ scp "adam@docs.nyc1.psf.io:/var/log/docsbuild/docsbuild*" docsbuild-logs/
   $ python check_times.py
"""

import gzip
import tomllib
from pathlib import Path

from build_docs import format_seconds

LOGS_ROOT = Path("docsbuild-logs").resolve()


def get_lines(filename: str = "docsbuild.log") -> list[str]:
    lines = []
    zipped_logs = list(LOGS_ROOT.glob(f"{filename}.*.gz"))
    zipped_logs.sort(key=lambda p: int(p.name.split(".")[-2]), reverse=True)
    for logfile in zipped_logs:
        with gzip.open(logfile, "rt", encoding="utf-8") as f:
            lines += f.readlines()
    with open(LOGS_ROOT / filename, encoding="utf-8") as f:
        lines += f.readlines()
    return lines


def calc_time(lines: list[str]) -> None:
    in_progress = False
    in_progress_line = ""

    print("Start                | Version | Language | Build          | Trigger")
    print(":--                  | :--:    | :--:     | --:            | :--:")

    for line in lines:
        line = line.strip()

        if "Saved new rebuild state for" in line:
            _, state = line.split("Saved new rebuild state for", 1)
            key, state_toml = state.strip().split(": ", 1)
            language, version = key.strip("/").split("/", 1)
            state_data = tomllib.loads(f"t = {state_toml}")["t"]
            start = state_data["last_build_start"]
            fmt_duration = format_seconds(state_data["last_build_duration"])
            reason = state_data["triggered_by"]
            print(
                f"{start:%Y-%m-%d %H:%M UTC} | {version: <7} | {language: <8} | {fmt_duration :<14} | {reason}"
            )

        if line.endswith("Build start."):
            in_progress = True
            in_progress_line = line

        if in_progress and ": Build done " in line:
            in_progress = False

        if ": Full build done" in line:
            timestamp = f"{line[:16]} UTC"
            _, fmt_duration = line.removesuffix(").").split("(")
            print(
                f"{timestamp: <20} | --FULL- | -BUILD-- | {fmt_duration :<14} | -----------"
            )

    if in_progress:
        start_timestamp = f"{in_progress_line[:16]} UTC"
        language, version = in_progress_line.split(" ")[3].removesuffix(":").split("/")
        print(
            f"{start_timestamp: <20} | {version: <7} | {language: <8} | In progress... | ..."
        )

    print()


if __name__ == "__main__":
    print("Build times (HTML only)")
    print("=======================")
    print()
    calc_time(get_lines("docsbuild-only-html.log"))

    print("Build times (no HTML)")
    print("=====================")
    print()
    calc_time(get_lines("docsbuild-no-html.log"))
