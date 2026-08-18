import os

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from orchestrator.cli import app, main

__all__ = ["app", "main"]


if __name__ == "__main__":
    import sys

    sys.exit(main())
