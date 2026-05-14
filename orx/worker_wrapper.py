from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO


def _forward(stream: TextIO, sink: TextIO, log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log_file:
        for line in iter(stream.readline, ""):
            log_file.write(line)
            log_file.flush()
            sink.write(line)
            sink.flush()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stdout-log", required=True, type=Path)
    parser.add_argument("--stderr-log", required=True, type=Path)
    parser.add_argument("--runtime-meta", required=True, type=Path)
    parser.add_argument("--shell-command", required=True)
    args = parser.parse_args()

    try:
        process = subprocess.Popen(
            args.shell_command,
            shell=True,
            executable="/bin/sh",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
    except Exception as exc:  # pragma: no cover
        args.runtime_meta.parent.mkdir(parents=True, exist_ok=True)
        args.runtime_meta.write_text(
            json.dumps(
                {
                    "exit_code": 127,
                    "error": str(exc),
                    "finished_at": datetime.now(UTC).isoformat(),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return 127

    stdout_thread = threading.Thread(
        target=_forward,
        args=(process.stdout, sys.stdout, args.stdout_log),
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=_forward,
        args=(process.stderr, sys.stderr, args.stderr_log),
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()
    exit_code = process.wait()
    stdout_thread.join()
    stderr_thread.join()

    args.runtime_meta.parent.mkdir(parents=True, exist_ok=True)
    args.runtime_meta.write_text(
        json.dumps(
            {
                "exit_code": exit_code,
                "finished_at": datetime.now(UTC).isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
