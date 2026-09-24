"""Migration must preserve evidence identity, including after relocating the repo."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from disk_bench.results_paths import resolve_result_path


class HistoricalEvidencePaths(unittest.TestCase):
    def test_longest_prefix_and_relocated_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / 'artifacts/graphs/gist/evidence.json'
            target.parent.mkdir(parents=True)
            target.write_bytes(b'{"original":true}\n')
            migration = ('/old/repo', {'results': 'results/archive',
                                      'results/graph': 'artifacts/graphs'})
            for old in ('results/graph/gist/evidence.json',
                        '/old/repo/results/graph/gist/evidence.json'):
                actual = resolve_result_path(old, repo_root=root, migration=migration)
                self.assertEqual(actual, target)
                self.assertEqual(actual.read_bytes(), b'{"original":true}\n')
            self.assertEqual(resolve_result_path('/unrelated/results/graph/a', repo_root=root,
                                               migration=migration), Path('/unrelated/results/graph/a'))
            self.assertEqual(resolve_result_path('results/graph_other/a', repo_root=root,
                                               migration=migration), root / 'results/archive/graph_other/a')

    def test_existing_path_is_not_rebound_to_another_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = root / 'results/a.json'
            original.parent.mkdir()
            original.write_text('unchanged')
            resolved = resolve_result_path('results/a.json', repo_root=root,
                                           migration=('/old', {'results': 'archive'}))
            self.assertEqual(resolved, original)


if __name__ == '__main__':
    unittest.main()
