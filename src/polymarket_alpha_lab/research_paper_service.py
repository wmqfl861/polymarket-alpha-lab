"""Read original complete execution history, then assemble supplied paper scenarios."""
from polymarket_alpha_lab.research_execution_psycopg import (
    inspect_captured_research_with_psycopg, load_captured_research_evaluation_with_psycopg,
)
from polymarket_alpha_lab.research_paper import (
    ResearchPaperEvaluation, scenarios_copy,
)


def evaluate_research_paper_with_psycopg(dsn, *, scenarios, **configuration):
    """No writes, retry, alternate loader or incomplete-history bypass.

    History and outcome visibility come from ONE original strict evaluation read.
    Per-scenario request lookups are later immutable-row reads, NOT one combined
    atomic snapshot. Composition checks each record against that original read.
    Missing/foreign requests fail the whole operation instead of being omitted.
    """
    copied = scenarios_copy(scenarios)
    evaluation = load_captured_research_evaluation_with_psycopg(dsn, **configuration)
    ids = {r.record_id for r in evaluation.records}
    if any(s.record_id not in ids for s in copied):
        raise ValueError('research_paper_record_not_in_evaluation')
    executions = tuple(inspect_captured_research_with_psycopg(dsn, record_id=s.record_id) for s in copied)
    return ResearchPaperEvaluation(evaluation=evaluation, scenarios=copied, executions=executions)
