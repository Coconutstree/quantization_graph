import importlib.util, unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location('comparison_driver',Path(__file__).with_name('run.py'))
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
class ProtocolTests(unittest.TestCase):
    def test_pooled_throughput_not_arithmetic_mean(self):
        self.assertAlmostEqual(r.qps([{'query_count':800,'qps':100},{'query_count':800,'qps':200}]),400/3)
    def test_only_intended_flags_change(self):
        s=r.sources('gist');template=r.load(s['baseline']/'command.json');before=r.protocol.flags(template)
        allowed={'--memory-policy','--memory-cache-bytes','--memory-profile-dir','--memory-stats-dir','--result-json','--query-trace','--run-id','--native-binary-sha256'}
        for mode in ['baseline','hot_payload','hybrid']:
            out=r.make_command(template,Path('/tmp/test-run'),Path('/tmp/test-profile'),mode,s['budget'],'digest')
            after=r.protocol.flags(out)
            self.assertEqual(set(before),set(after))
            self.assertEqual({k:v for k,v in before.items() if k not in allowed},{k:v for k,v in after.items() if k not in allowed})
            self.assertEqual(int(after['--memory-cache-bytes']),0 if mode=='baseline' else s['budget'])
    def test_rejects_search_parameter_drift(self):
        s=r.sources('gist');template=r.load(s['baseline']/'command.json');i=template.index('--workers');template[i+1]='16'
        with self.assertRaises(AssertionError):r.make_command(template,Path('/tmp/test-run'),Path('/tmp/test-profile'),'hybrid',s['budget'],'digest')
if __name__=='__main__':unittest.main()
