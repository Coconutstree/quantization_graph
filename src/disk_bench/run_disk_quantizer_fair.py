"""05A compatibility entry point; runs only formal 01 native disk ports."""

from orchestrator import run


if __name__ == "__main__":
    raise SystemExit(run(default_layer="05a"))
