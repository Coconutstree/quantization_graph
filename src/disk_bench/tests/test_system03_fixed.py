import copy
import csv
import importlib.util
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT/'src'))
from disk_bench import system03
from disk_bench.orchestrator import make_parser, configure_experiment

class System03Tests(unittest.TestCase):
    def args(self, *extra):
        a = make_parser('03').parse_args(['--phase','run', *extra])
        configure_experiment(a,'03')
        return a

    def test_default_and_rejected_alternatives(self):
        a=self.args(); system03.apply_defaults(a)
        self.assertEqual(tuple(a.methods.split(',')),system03.METHODS)
        self.assertEqual(a.fixed_beam,4)
        self.assertEqual(a.search_widths,system03.WIDTHS)
        self.assertTrue(a.method_pipeline)
        for options in [('--fixed-beam','8'),('--repeats','2'),('--methods','SymphonyQG-DiskPort'),
                        ('--ours-cache-allocation','records_only'),('--search-widths','60,100')]:
            with self.subTest(options=options), self.assertRaises(ValueError):
                system03.apply_defaults(self.args(*options))

    def test_shared_05_defaults_unchanged(self):
        a=make_parser('05').parse_args(['--phase','run'])
        configure_experiment(a,'05'); before=vars(copy.deepcopy(a))
        system03.apply_defaults(a)
        self.assertEqual(vars(a),before)

    def test_no_tuning(self):
        with self.assertRaisesRegex(ValueError,'no performance tuning'):
            system03.apply_defaults(self.args('--phase','tune'))

    def test_storage_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            for rotational in (True,None):
                with patch('disk_bench.common.lsblk_rotational',return_value=rotational):
                    self.assertTrue(system03.storage_errors(Path(tmp)))
            with patch('disk_bench.common.lsblk_rotational',return_value=False):
                self.assertEqual(system03.storage_errors(Path(tmp)),[])
            self.assertTrue(system03.storage_errors(Path(tmp)/'missing'))

    def test_content_disjoint_not_just_filenames(self):
        with tempfile.TemporaryDirectory() as tmp:
            a,b=Path(tmp)/'warm.fvecs',Path(tmp)/'test.fvecs'
            a.write_bytes(struct.pack('<ifif',1,1.,1,2.))
            b.write_bytes(struct.pack('<if',1,3.))
            evidence=system03.verify_disjoint(a,b,2)
            self.assertTrue(evidence['warmup_test_disjoint'])
            b.write_bytes(struct.pack('<if',1,2.))
            with self.assertRaisesRegex(ValueError,'overlap'):
                system03.verify_disjoint(a,b,2)
            with self.assertRaisesRegex(ValueError,'count'):
                system03.verify_disjoint(a,b,3)

    def test_immutable_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            f=Path(tmp)/'lock.json'
            system03.freeze(f,{'beam':4}); system03.freeze(f,{'beam':4})
            with self.assertRaisesRegex(ValueError,'frozen'):
                system03.freeze(f,{'beam':8})

    def test_exact_curve_coverage(self):
        rows=[dict(method=m,search_width=w,beam_width=4,workers=32,repeat_id=0,
                   search_dram_budget_gib=4,system03_protocol=system03.PROTOCOL)
              for m in system03.METHODS for w in system03.WIDTHS]
        system03.check_rows(rows)
        for bad in [rows[:-1],rows+[rows[0]], [{**r,'system03_protocol':''} for r in rows]]:
            with self.assertRaises(ValueError): system03.check_rows(bad)

    def test_starling_layout(self):
        self.assertIsNone(system03.unsupported_reason('Starling-Disk',960,48))
        self.assertTrue(system03.unsupported_reason('Starling-Disk',960,64))
        self.assertTrue(system03.unsupported_reason('Starling-Disk',1024,48))
        self.assertIsNone(system03.unsupported_reason('AiSAQ-Disk',1536))

    def test_protocol_survives_csv_export(self):
        from disk_bench.native_contract import atomic_write_csv
        rows=[dict(method=m,search_width=w,beam_width=4,workers=32,repeat_id=0,
                   search_dram_budget_gib=4,system03_protocol=system03.PROTOCOL)
              for m in system03.METHODS for w in system03.WIDTHS]
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'points.csv';atomic_write_csv(path,rows)
            with path.open() as stream: restored=list(csv.DictReader(stream))
            system03.check_rows(restored)

if __name__=='__main__': unittest.main()
