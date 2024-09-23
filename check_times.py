"""Check the frequency of the rebuild loop.

This must be run in a directory that has the ``docsbuild.log*`` files.
For example:

.. code-block:: bash

   $ scp "adam@docs.nyc1.psf.io:/var/log/docsbuild/docsbuild.log*" docsbuild-logs
   $ python check_times.py
"""

import datetime as dt
import gzip
from pathlib import Path

from build_docs import format_seconds


def get_lines() -> list[str]:
    lines = []
    zipped_logs = list(Path.cwd().glob("docsbuild.log.*.gz"))
    zipped_logs.sort(key=lambda p: int(p.name.split(".")[-2]), reverse=True)
    for logfile in zipped_logs:
        with gzip.open(logfile, "rt", encoding="utf-8") as f:
            lines += f.readlines()
    with open("docsbuild.log", encoding="utf-8") as f:
        lines += f.readlines()
    return lines


def calc_time(lines: list[str]) -> None:
    start = end = language = version = start_timestamp = None
    reason = lang_ver = ""

    print("Start                | Version | Language | Build          | Trigger")
    print(":--                  | :--:    | :--:     | --:            | :--:")

    for line in lines:
        line = line.strip()

        if ": Should rebuild: " in line:
            if "no previous state found" in line:
                reason = "brand new"
            elif "new translations" in line:
                reason = "translation"
            elif "Doc/ has changed" in line:
                reason = "docs"
            else:
                reason = ""
            lang_ver = line.split(" ")[3].removesuffix(":")

        if line.endswith("Build start."):
            timestamp = line[:23].replace(",", ".")
            language, version = line.split(" ")[3].removesuffix(":").split("/")
            start = dt.datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S.%f")
            start_timestamp = f"{line[:16]} UTC"

        if start and ": Build done " in line:
            timestamp = line[:23].replace(",", ".")
            language, version = line.split(" ")[3].removesuffix(":").split("/")
            end = dt.datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S.%f")

        if start and end:
            duration = (end - start).total_seconds()
            fmt_duration = format_seconds(duration)
            if lang_ver != f"{language}/{version}":
                reason = ""
            print(
                f"{start_timestamp: <20} | {version: <7} | {language: <8} | {fmt_duration :<14} | {reason}"
            )
            start = end = start_timestamp = None

        if ": Full build done" in line:
            timestamp = f"{line[:16]} UTC"
            _, fmt_duration = line.removesuffix(").").split("(")
            print(
                f"{timestamp: <20} | --FULL- | -BUILD-- | {fmt_duration :<14} | -----------"
            )

    if start and end is None:
        print(
            f"{start_timestamp: <20} | {version: <7} | {language: <8} | In progress... | {reason}"
        )


if __name__ == "__main__":
    calc_time(get_lines())
