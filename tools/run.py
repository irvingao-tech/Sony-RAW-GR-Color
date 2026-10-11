"""Run the source checkout without an editable package installation."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from s2g.cli import main

raise SystemExit(main())
