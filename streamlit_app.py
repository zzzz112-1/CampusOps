"""CampusOps demo entry."""
from pathlib import Path
import sys
root = Path(__file__).absolute().parent
if (root / "src/campusops/reviewer.py").is_file():
    sys.path.insert(0, str(root / "src"))
from campusops.reviewer import run_ui
if __name__ == "__main__":
    run_ui()
