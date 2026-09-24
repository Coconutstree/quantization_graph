"""05C compatibility entry point; requires all five formal native disk ports."""

from orchestrator import run


if __name__ == "__main__":
    raise SystemExit(run(default_layer="05c"))
