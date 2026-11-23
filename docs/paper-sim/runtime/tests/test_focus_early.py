"""Early football focus regression through the production scan and combined shortlist."""
from datetime import timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_football_focus import NOW, make_fixture, make_market, make_books, make_match
import football_focus as focus
import football_feed as feed
import football_forward as forward
import sports_focus as joint


def unpublished(row, *, observed=None, injury=False):
    payload,general,status,at=make_match(row,observed=observed or NOW,confirmed=True)
    lineup=payload['content']['lineup']
    for side in ('homeTeam','awayTeam'):
        lineup[side].pop('formation',None)
        lineup[side]['starters']=[]
    if injury:
        lineup['homeTeam']['unavailable']=[{'id':'401','unavailability':{'type':'injury'}}]
    return payload,general,status,at


def fixture(row):
    return focus.Fixture(row,(),{})


class EarlyWatchTests(unittest.TestCase):
    def test_zero_stake_scan_render_and_notice_dedup(self):
        row=make_fixture(); market=make_market(1);active={focus.admission.scope_key('epl','total',2.5,None,feed.MODEL_VERSION_SOCCER)}
        db=SimpleNamespace(execute=lambda *_: SimpleNamespace(fetchall=lambda:[row]))
        with (patch.object(focus.admission,'active_scopes',return_value=active),
              patch.object(forward,'_markets',return_value=([market],True)),
              patch.object(forward,'_books',return_value=make_books(market)),
              patch.object(forward,'_get_match',return_value=unpublished(row)) as identity,
              patch.object(feed,'_verify_live_contract',return_value=market.raw) as verify):
            result=focus.scan(db,NOW,NOW+timedelta(hours=24))
            self.assertEqual(result['status_counts'],{'focus_candidate':1})
            selected,=result['candidates']
            self.assertTrue(selected['lineup_pending'])
            self.assertEqual((selected['status'],selected['stake_fen']),('watch',0))
            self.assertEqual(len(selected['book']),2)
            self.assertTrue(all(leg['filled_usd']>=250 for leg in selected['book'].values()))
            checked=result['as_of']; other_side=selected|{'side':'under','direction':'another direction'}
            self.assertEqual(joint.pick([selected,other_side],checked,NOW+timedelta(days=1),set()),[selected])
            self.assertEqual(joint.pick([selected],checked,NOW+timedelta(days=1),set(),{selected['fixture_key']}),[])
            self.assertEqual(joint.pick([selected],checked,NOW+timedelta(days=1),{('football',selected['fixture_key'])}),[])
            self.assertIn('模拟 ¥0.00',joint.render([selected]))
            self.assertIn('首发待公布',joint.render([selected]))
            for confirmed,label in ((False,'预测首发，待官宣'),(True,'')):
                with self.subTest(confirmed=confirmed):
                    identity.return_value=make_match(row,confirmed=confirmed)
                    rendered_entry,=focus.scan(db,NOW,NOW+timedelta(hours=24))['candidates']
                    self.assertEqual(rendered_entry['lineup_type'],'confirmed' if confirmed else 'predicted')
                    self.assertFalse(rendered_entry['lineup_pending'])
                    content=joint.render([rendered_entry])
                    self.assertIn('模拟 ¥0.00',content)
                    if label:
                        self.assertIn(label,content)
                    else:
                        self.assertNotIn('待官宣',content)
                    self.assertNotIn('首发待公布',content)
            verify.return_value=None
            self.assertEqual(focus.scan(db,NOW,NOW+timedelta(hours=24))['status_counts'],{'direct_contract_unverified':1})

    def test_fresh_identity_valid_both_availability_lists_and_no_partial_XI(self):
        row=make_fixture()
        self.assertIsNotNone(focus._fixture_lineup(fixture(row),unpublished(row),lambda:NOW)[0])
        for side,field,value in (('awayTeam','unavailable',None),('homeTeam','unavailable',[{'id':'111','unavailability':{}}]),
                                 ('homeTeam','starters',[{'id':'149'}]),('awayTeam','id','987654')):
            with self.subTest(side=side,field=field):
                match=unpublished(row)
                match[0]['content']['lineup'][side][field]=value
                self.assertIsNone(focus._fixture_lineup(fixture(row),match,lambda:NOW)[0])
        match=unpublished(row,observed=NOW-feed.BOOK_AGE-timedelta(seconds=1))
        self.assertEqual(focus._fixture_lineup(fixture(row),match,lambda:NOW),(None,'lineups_stale'))
        changed=unpublished(row);changed[2]['started']=True
        self.assertIsNone(focus._fixture_lineup(fixture(row),changed,lambda:NOW)[0])

    def test_prior_injuries_conflict_and_published_XI_retraction(self):
        for prior in (unpublished(make_fixture(),observed=NOW-timedelta(minutes=5),injury=True),
                      make_match(make_fixture(),observed=NOW-timedelta(minutes=5),confirmed=True)):
            row=make_fixture()
            row['info']['review_context']=feed._lineup_evidence(prior[0],row['info'],prior[3],
                                              focus.model.details_url(row['info']['fixture_id']))
            self.assertIsNone(focus._fixture_lineup(fixture(row),unpublished(row),lambda:NOW)[0])
        row=make_fixture()
        self.assertEqual(focus._fixture_lineup(fixture(row),make_match(row,injury=True),lambda:NOW),
                         (None,'lineup_conflict'))

    def test_T90_boundary_and_kickoff(self):
        for seconds,allowed in ((90*60+1,True),(90*60,False),(89*60,False),(55*60,False),(30*60,False),(1,False),(0,False),(-1,False)):
            with self.subTest(seconds_to_kickoff=seconds):
                row=make_fixture(kickoff=NOW+timedelta(seconds=seconds))
                match=unpublished(row,observed=min(NOW,row['kickoff']-timedelta(seconds=1)))
                ctx,reason=focus._fixture_lineup(fixture(row),match,lambda:NOW)
                self.assertEqual(ctx is not None,allowed)
                if seconds<=0:self.assertEqual(reason,'at_or_after_kickoff')

    def test_new_official_review_window_is_reserved_for_formal_decision(self):
        row=make_fixture(kickoff=NOW+timedelta(minutes=55))
        baseline=make_match(row,observed=NOW-timedelta(minutes=3),confirmed=True)
        row['info'][feed.CONFIRMED_BASELINE]=feed._lineup_evidence(baseline[0],row['info'],baseline[3],
                                                    focus.model.details_url(row['info']['fixture_id']))
        self.assertEqual(focus._fixture_lineup(fixture(row),make_match(row,confirmed=True),lambda:NOW),
                         (None,'current_formal_review'))


if __name__=='__main__':unittest.main()
