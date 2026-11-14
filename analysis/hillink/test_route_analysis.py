"""route_analysis (시간대 동선 재구성 · 필터 · RevPAR) 테스트."""

import random
import unittest
from datetime import date

import route_analysis as ra


def item(venue_kor: str, product: str = 'x', price: float = 100_000) -> dict:
    return {'업장': venue_kor, '상품': product, '결제액': str(price), '구매 수량': '1'}


class HourShareTest(unittest.TestCase):
    def test_shares_sum_to_dwell_hours(self):
        for start, dwell in ((7.0, 120), (11.5, 75), (18.25, 90), (23.5, 60)):
            shares = list(ra._hour_shares(start, dwell))
            self.assertAlmostEqual(sum(share for _, share in shares), dwell / 60, places=9)

    def test_shares_stay_inside_visit_interval(self):
        shares = dict(ra._hour_shares(7.5, 120))
        self.assertAlmostEqual(shares[7], 0.5)
        self.assertAlmostEqual(shares[8], 1.0)
        self.assertAlmostEqual(shares[9], 0.5)


class FilterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = ra.load_inputs()
        cls.rooms = cls.data['room_products']
        cls.day = date(2026, 10, 15)  # 목요일
        cls.rng = staticmethod(lambda: random.Random(1))

    def _schedule(self, items, party=2, child=False):
        return ra.schedule_day(items, self.day, party, child, self.rng(), self.rooms)

    def test_closed_venue_rejects_day(self):
        closed_day = None
        for offset in range(33):
            day = date.fromordinal(date(2026, 10, 1).toordinal() + offset)
            if not ra._windows('THEATRE_OF_LIGHT', day):
                closed_day = day
                break
        self.assertIsNotNone(closed_day, '기간 내 빛의 시어터 폐장일이 있어야 한다')
        result = ra.schedule_day(
            [item('빛의 시어터')], closed_day, 2, False, random.Random(1), self.rooms
        )
        self.assertFalse(result.stayed)
        self.assertEqual(result.reason, 'CLOSED')

    def test_child_with_bar_rejected(self):
        result = self._schedule([item('럭스바'), item('피자힐')], party=2, child=True)
        self.assertFalse(result.stayed)
        self.assertEqual(result.reason, 'COMPOSITION')

    def test_party_over_capacity_rejected(self):
        result = self._schedule([item('럭스바')], party=999)
        self.assertFalse(result.stayed)
        self.assertEqual(result.reason, 'CAPACITY')

    def test_duplicate_seated_meal_rejected(self):
        items = [item('더뷔페'), item('명월관'), item('온달'), item('금룡')]
        result = self._schedule(items, party=2, child=False)
        self.assertFalse(result.stayed)
        self.assertEqual(result.reason, 'SLOT_DUP')

    def test_breakfast_plus_dinner_is_feasible(self):
        result = self._schedule([item('더뷔페'), item('명월관')], party=2, child=False)
        self.assertTrue(result.stayed)

    def test_room_products_are_not_visits(self):
        result = self._schedule([item('그랜드', '캠크닉 인더 포레스트 TYPE 1')], party=2)
        self.assertFalse(result.stayed)
        self.assertEqual(result.reason, 'CLOSED')

    def test_accepted_assignments_never_overlap(self):
        items = [item('더뷔페'), item('피자힐'), item('명월관'), item('파빌리온')]
        result = self._schedule(items, party=2, child=False)
        if not result.stayed:
            self.skipTest('이 조합은 배치 불가로 판정됨')
        for left, right in zip(result.assignments, result.assignments[1:]):
            left_end = left.start + left.dwell / 60
            gap = 0 if 'IRD' in (left.venue, right.venue) else ra.TRAVEL_MIN / 60
            self.assertLessEqual(left_end + gap, right.start + 1e-9)


class AnalyzeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = ra.analyze(500, 17)

    def test_deterministic(self):
        again = ra.analyze(500, 17)
        self.assertEqual(self.result['acceptance_rate'], again['acceptance_rate'])
        self.assertEqual(self.result['revpash'], again['revpash'])
        self.assertEqual(self.result['hour_totals'], again['hour_totals'])

    def test_rejections_are_known_reasons(self):
        self.assertTrue(set(self.result['rejections']) <= set(ra.REJECTIONS))

    def test_acceptance_rate_dominant(self):
        self.assertGreater(self.result['acceptance_rate'], 0.85)

    def test_revpar_matches_definition(self):
        expected = self.result['room_revenue'] / self.result['available_room_nights']
        self.assertAlmostEqual(self.result['revpar'], expected, places=6)
        self.assertAlmostEqual(
            self.result['occupancy'],
            self.result['room_units'] / self.result['available_room_nights'],
            places=9,
        )
        self.assertEqual(self.result['rooms_total'], sum(info['n'] for info in ra.ROOMS.values()))

    def test_hour_totals_match_venue_matrix(self):
        hours = sum(self.result['hour_totals'].values())
        matrix = sum(sum(row.values()) for row in self.result['hour_venue'].values())
        self.assertAlmostEqual(hours, matrix, delta=1.0)

    def test_evening_peak(self):
        totals = self.result['hour_totals']
        peak = max(totals, key=totals.get)
        self.assertIn(peak, ('12:00', '13:00', '18:00', '19:00'))


if __name__ == '__main__':
    unittest.main()
