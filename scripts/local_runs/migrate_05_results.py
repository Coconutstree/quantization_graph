"""Move live result trees without invalidating in-flight absolute paths."""
import json
import os
from pathlib import Path
import signal

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "results/archive/legacy_layout_20260918/disk_environment/05_disk_system_fair"
TARGET = BASE / "test_L_400_w_32"


def main():
    runtime = TARGET / "_runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    moves = [
        (BASE / "agnews_acceptance_20260912", runtime / "agnews_acceptance_20260912"),
        (BASE / "single_test_w32_20260911", runtime / "single_test_w32_20260911"),
        (runtime / "agnews_acceptance_20260912/agnews", TARGET / "agnews"),
        (runtime / "single_test_w32_20260911/gist", TARGET / "gist"),
        (runtime / "agnews_acceptance_20260912/exports", TARGET / "exports"),
        (runtime / "agnews_acceptance_20260912/references", TARGET / "agnews/_references"),
    ]
    if any(dst.exists() for _, dst in moves):
        raise RuntimeError("destination exists; refusing to overwrite results")
    paused = []
    journal = []
    try:
        for proc in Path("/proc").glob("[0-9]*"):
            if int(proc.name) == os.getpid():
                continue
            try:
                args = (proc / "cmdline").read_bytes().split(b"\0")
                exe = Path(os.fsdecode(args[0])).name
                command = b" ".join(args)
                relevant = (exe.startswith("qgraph05_") or
                            (exe.startswith("python") and any(name in command for name in
                             [b"run_agnews_acceptance.py", b"resume_05c_single_test.py",
                              b"rerun_agnews_locality_then_resume.py", b"check_03c_query_cache.py",
                              b"plot_05c_recall_qps.py", b"export_05c_pending_figures.py"])))
                stat = (proc / "stat").read_text().rsplit(")", 1)[1].split()
                if relevant and stat[0] not in {"T", "t", "Z"}:
                    os.kill(int(proc.name), signal.SIGSTOP)
                    paused.append((int(proc.name), stat[19]))
            except (FileNotFoundError, ProcessLookupError, PermissionError):
                continue
        for source, dest in moves:
            inode = source.stat().st_ino
            source.rename(dest)
            source.symlink_to(dest, target_is_directory=True)
            if source.resolve() != dest.resolve() or dest.stat().st_ino != inode:
                raise RuntimeError("migration identity check failed")
            journal.append({"old_path": str(source), "new_path": str(dest), "inode": inode})
        for dataset in ("dbpedia", "sift10m"):
            source = runtime / "single_test_w32_20260911" / dataset
            dest = TARGET / dataset
            if source.exists() or dest.exists():
                raise RuntimeError("unexpected existing pending dataset")
            dest.mkdir()
            source.symlink_to(dest, target_is_directory=True)
            journal.append({"old_path": str(source), "new_path": str(dest), "pending": True})
        (TARGET / "migration.json").write_text(json.dumps(journal, indent=2) + "\n")
        print(json.dumps(journal, indent=2))
    finally:
        for pid, start in reversed(paused):
            try:
                stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
                if stat[19] == start:
                    os.kill(pid, signal.SIGCONT)
            except (FileNotFoundError, ProcessLookupError):
                pass


if __name__ == "__main__":
    main()
