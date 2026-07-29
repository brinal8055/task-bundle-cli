#!/usr/bin/env python3
from pathlib import Path

Path("answer.txt").write_text("42\n", encoding="utf-8")
