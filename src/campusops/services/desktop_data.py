"""Reviewer automatic fictional bootstrap; no original desktop state access."""
from pathlib import Path
from campusops.reviewer import ReviewerContext, initialize_data
def initialize_desktop_data(context: ReviewerContext, root: Path):
    if not isinstance(context, ReviewerContext) or Path(root).absolute() != context.root:
        raise ValueError("Reviewer project root mismatch")
    return initialize_data(context)
