#!/usr/bin/env python3
"""Good News Evening Briefing -- run the pipeline.

Convenience launcher so the package can be run from a source checkout without
installing it. Equivalent to `python3 -m good_news` once installed.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "src"))

from good_news.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
