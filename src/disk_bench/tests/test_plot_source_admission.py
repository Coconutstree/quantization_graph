import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from plot_05_disk_suite import _validate_csv_sources, ContractError, _display_label


class PlotSourceTests(unittest.TestCase):
    def setUp(self):
        self.source = dict(artifact_path='/tmp/source.json', config_id='native', search_width=40,
                           beam_width=1, ablation='', qps=12.5, formal_ready=True)
        self.row = {k: str(v) for k, v in self.source.items()}

    def test_identical_measurement(self):
        _validate_csv_sources([self.row], [self.source])

    def test_rehashed_csv_with_changed_qps_is_rejected(self):
        self.row['qps'] = '1250'
        with self.assertRaisesRegex(ContractError, 'qps'):
            _validate_csv_sources([self.row], [self.source])

    def test_duplicate_point_is_rejected(self):
        with self.assertRaises(ContractError):
            _validate_csv_sources([self.row, self.row], [self.source])

    def test_adaptation_label(self):
        self.assertIn('our disk adaptation', _display_label('SymphonyQG-DiskPort'))


if __name__ == '__main__':
    unittest.main()
