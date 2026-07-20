#!/usr/bin/env python3
"""Helper: submit a PDF to MinerU Stage 1 only.
Bypasses bash encoding issues with Chinese paths.
Usage: python _submit_mineru.py "<pdf_path>"
"""
import os, subprocess, sys
from pathlib import Path

PYTHON = Path(__file__).parent / ".venv" / "Scripts" / "python.exe"
PIPELINE = Path(__file__).parent / "pipeline.py"

pdf = sys.argv[1]
env = {k: v for k, v in os.environ.items()}
for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    env.pop(k, None)

result = subprocess.run(
    [str(PYTHON), str(PIPELINE), pdf, "--skip-deepseek"],
    env=env,
)
sys.exit(result.returncode)
