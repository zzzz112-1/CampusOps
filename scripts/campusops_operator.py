"""Review-only CLI; original .env and desktop session interfaces are not used."""
from pathlib import Path
import sys
root = Path(__file__).absolute().parents[1]
if (root / 'src/campusops').is_dir():
    sys.path.insert(0, str(root / 'src'))
from campusops.cli import main
if __name__ == '__main__':
    raise SystemExit(main())