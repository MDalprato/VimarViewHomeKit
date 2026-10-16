import unittest
from vimar_watch import Tracker


def record(time, channel='2'):
    return {'starttime': f'2026-10-16T{time}Z', 'channel': channel}


class TrackingTests(unittest.TestCase):
    def test_baseline_then_one_new_ring(self):
        t = Tracker()
        old, new = record('14:50:07'), record('14:53:08')
        self.assertEqual(t.update('video', [old]), [])
        self.assertEqual(t.update('video', [new, old]), [new])
        self.assertEqual(t.update('video', [new, old]), [])

    def test_truncated_results_do_not_replay_old_events(self):
        t = Tracker()
        old, new = record('14:50:07'), record('14:53:08')
        t.update('video', [new, old])
        self.assertEqual(t.update('video', [old]), [])
        self.assertEqual(t.update('video', []), [])
        self.assertEqual(t.update('video', [new, old]), [])

    def test_empty_initial_inventory_uses_device_time(self):
        t = Tracker()
        self.assertEqual(t.update('video', []), [])
        new = record('00:00:01')
        self.assertEqual(t.update('video', [new]), [new])

    def test_picture_burst_deduplicated_per_channel(self):
        t = Tracker()
        t.update('picture', [record('14:50:07')])
        new = record('14:53:08')
        self.assertEqual(t.update('picture', [new, new]), [new])


if __name__ == '__main__':
    unittest.main()
