from dataclasses import replace
from decimal import Decimal as D
from fractions import Fraction as F
import json, random
from tests.test_research_paper import fixture,run,book,Costs
rng=random.Random(3415)
checked=0;rejected=0
for _ in range(240):
    h,s,e=fixture(p='0.99')
    prices=sorted(rng.sample(range(5,91),4))
    levels=tuple((str(D(p)/100),str(rng.randrange(1,7))) for p in prices)
    qty=rng.randrange(1,sum(int(q) for _,q in levels)+1)
    rate=D(rng.randrange(0,101))/100
    s=replace(s,requested_size=D(qty),costs=Costs(rate,D('.001'),D('.002'),D('.003'),D('.004'),D('.005')),
        gates=replace(s.gates,min_net_edge=D('0'),max_spread=D('1')),
        yes_book=book('101',e.request.intake.condition_id,s.decision_at,asks=levels,bids=(('0.001','10'),)))
    row=run(h,s,e)['paper_attempts'][0]
    if row['selected_side']=='none': rejected+=1;continue
    assert row['selected_side']=='yes'
    remaining=qty;entry=F(0);fee=F(0)
    for ps,qs in levels:
        q=min(remaining,int(qs));p=F(ps)
        entry+=q*p;fee+=q*F(rate)*p*(1-p);remaining-=q
    nonfee=qty*F('.015')
    totals=row['assumed_totals']
    assert F(totals['entry_notional_upper_bound'])>=entry
    assert F(totals['assumed_fee_upper_bound'])>=fee
    assert F(totals['assumed_total_cost_upper_bound'])>=entry+fee+nonfee
    assert F(totals['expected_net_edge_lower_bound'])<=qty*F('.99')-entry-fee-nonfee
    checked+=1
print(json.dumps(dict(cases=240,ready_with_independent_fraction_bounds=checked,rejected=rejected,seed=3415)))
