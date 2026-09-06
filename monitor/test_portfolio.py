import tempfile, unittest
from pathlib import Path
from unittest.mock import patch
import store, portfolio
class PortfolioTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.p=patch.object(store,'DATA',Path(self.tmp.name));self.p.start()
  self.base=dict(name='TEST',chain='bsc',ca='0x'+'a'*40,observed_at='2026-09-05T00:00:00+08:00',price=1,source='windvane',verified=True,age_days=2,market_cap=300000,fomo_ratio_lower=20)
 def tearDown(self):self.p.stop();self.tmp.cleanup()
 def test_baseline_immutable_and_qualified(self):
  with self.assertRaises(ValueError):portfolio.register(dict(self.base,verified=False))
  self.assertTrue(portfolio.register(self.base));self.assertFalse(portfolio.register(self.base))
  with self.assertRaises(ValueError):portfolio.register(dict(self.base,price=2))
 def test_sample_confirmation_and_report(self):
  portfolio.register(self.base)
  self.assertTrue(portfolio.report()['tokens'][0]['awaiting_sample'])
  sample=dict(self.base,observed_at='2026-09-05T01:00:00+08:00',price=2,confirmation=True)
  portfolio.add_sample(sample);r=portfolio.report();t=r['tokens'][0]
  self.assertEqual(r['principal'],100);self.assertFalse(t['awaiting_sample']);self.assertFalse(t['latest']['confirmation'])
  self.assertAlmostEqual(t['simulation']['models'][1]['cash'],100)
  with self.assertRaises(ValueError):portfolio.add_sample(dict(sample,price=3))
  self.assertAlmostEqual(portfolio.report(0,0,official=False)['tokens'][0]['simulation']['models'][0]['pnl'],100)

 def test_official_profile_marks_incomplete_costs(self):
  portfolio.register(self.base)
  t=portfolio.report()['tokens'][0]
  self.assertEqual(t['fees']['schedule'],'evm')
  self.assertFalse(t['fees']['complete'])
  self.assertAlmostEqual(t['simulation']['models'][0]['paid_fees'],.5)
  self.assertAlmostEqual(t['simulation']['models'][0]['net_value'],99.0025)
  self.assertEqual(portfolio.fee_profile('sol')['schedule'],'solana')
  with self.assertRaises(ValueError):portfolio.fee_profile('unknown')

 def test_watch_state_requires_three_distinct_failed_rounds(self):
  def point(hour, eligible, round_id):
   return dict(observed_at=f'2026-09-05T{hour:02d}:00:00+08:00', price=1,
               source='windvane', watch_eligible=eligible, watch_round=round_id,
               eligibility_reason='市值不在范围')
  self.assertEqual(portfolio.watch_state([point(1,False,'r1')])['status'],'dropping')
  state=portfolio.watch_state([point(1,False,'r1'),point(2,False,'r2')])
  self.assertEqual(state['ineligible_streak'],2)
  self.assertEqual(portfolio.watch_state([point(1,False,'r1'),point(2,False,'r2'),point(3,False,'r3')])['status'],'removed')
  self.assertEqual(portfolio.watch_state([point(1,False,'r1'),point(2,True,'r2')])['ineligible_streak'],0)
