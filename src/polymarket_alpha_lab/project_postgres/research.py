"""Application integration: no externally supplied DSN or default DB discovery."""
from threading import Condition

from .binding import bound
from .files import fail


class ProjectResearchSession:
    __slots__ = ('_database', '_identity', '_active', '_condition', '_in_flight')

    def __init__(self, database, identity):
        self._database = database
        self._identity = dict(identity)
        self._active = True
        self._condition = Condition()
        self._in_flight = 0

    def __repr__(self):
        return 'ProjectResearchSession(credentials=<private>)'

    def close(self):
        with self._condition:
            self._active = False
            while self._in_flight:
                self._condition.wait()

    def _call(self, operation, **kwargs):
        with self._condition:
            if not self._active:
                fail('project_postgres_session_closed')
            self._in_flight += 1
        try:
            i = self._identity
            with bound(i['instance_id'], i['root_sha256'], i['system_identifier']):
                return operation(self._database._dsn(i), **kwargs)
        finally:
            with self._condition:
                self._in_flight -= 1
                self._condition.notify_all()

    def run_research(self, *, request, model_factory):
        from polymarket_alpha_lab.research_execution_psycopg import run_captured_research_with_psycopg
        return self._call(run_captured_research_with_psycopg, request=request, model_factory=model_factory)

    def launch_crypto_research(self, **configuration):
        from polymarket_alpha_lab.research_crypto_launch_service import launch_crypto_research_with_psycopg
        return self._call(launch_crypto_research_with_psycopg, **configuration)

    def enqueue_research_batch(self, *, batch, allow_queue_write=False):
        from polymarket_alpha_lab.research_dispatch_store import enqueue_research_batch_with_psycopg
        return self._call(enqueue_research_batch_with_psycopg, batch=batch, allow_queue_write=allow_queue_write)

    def inspect_research_batch(self, *, batch_id):
        from polymarket_alpha_lab.research_dispatch_store import load_research_batch_with_psycopg
        return self._call(load_research_batch_with_psycopg, batch_id=batch_id)

    def run_research_batch(self, **configuration):
        from polymarket_alpha_lab.research_dispatch_runner import run_research_batch_with_psycopg
        return self._call(run_research_batch_with_psycopg, **configuration)

    def run_research_rotation(self, **configuration):
        from polymarket_alpha_lab.research_dispatch_rotation_runner import run_research_rotation_with_psycopg
        return self._call(run_research_rotation_with_psycopg, **configuration)

    def inspect_research_turn(self, *, rotation_id, turn_id):
        from polymarket_alpha_lab.research_dispatch_rotation_store import inspect_research_turn_with_psycopg
        return self._call(inspect_research_turn_with_psycopg, rotation_id=rotation_id, turn_id=turn_id)

    def create_model_budget(self, *, policy, allow_budget_write=False):
        from polymarket_alpha_lab.research_model_budget_store import create_model_budget_with_psycopg
        return self._call(create_model_budget_with_psycopg, policy=policy, allow_budget_write=allow_budget_write)

    def inspect_model_budget(self, *, budget_id):
        from polymarket_alpha_lab.research_model_budget_store import load_model_budget_with_psycopg
        return self._call(load_model_budget_with_psycopg, budget_id=budget_id)

    def run_budgeted_research(self, **configuration):
        from polymarket_alpha_lab.research_model_budget_runner import run_budgeted_research_with_psycopg
        return self._call(run_budgeted_research_with_psycopg, **configuration)

    def inspect(self, *, record_id):
        from polymarket_alpha_lab.research_execution_psycopg import inspect_captured_research_with_psycopg
        return self._call(inspect_captured_research_with_psycopg, record_id=record_id)

    def execution_inventory(self, **configuration):
        from polymarket_alpha_lab.research_execution_inventory import load_execution_inventory_with_psycopg
        return self._call(load_execution_inventory_with_psycopg, **configuration)

    def retry_capture(self, *, request, run):
        from polymarket_alpha_lab.research_execution_psycopg import retry_research_capture_with_psycopg
        return self._call(retry_research_capture_with_psycopg, request=request, run=run)

    def capture_outcome(self, *, condition_id, market_slug, resolved_at, actual_yes,
                        source_reference, source_content_sha256):
        from polymarket_alpha_lab.research_capture_psycopg import capture_research_outcome_with_psycopg
        return self._call(capture_research_outcome_with_psycopg, condition_id=condition_id,
            market_slug=market_slug, resolved_at=resolved_at, actual_yes=actual_yes,
            source_reference=source_reference, source_content_sha256=source_content_sha256)

    def record_resolution(self, *, submission):
        from polymarket_alpha_lab.research_resolution_store import record_resolution_review_with_psycopg
        return self._call(record_resolution_review_with_psycopg, submission=submission)

    def confirm_crypto_resolution(self, *, instruction, allow_resolution_write=False):
        from polymarket_alpha_lab.research_resolution_confirmation import confirm_crypto_resolution_with_psycopg
        return self._call(confirm_crypto_resolution_with_psycopg, instruction=instruction,
            allow_resolution_write=allow_resolution_write)

    def inspect_resolution(self, *, review_id):
        from polymarket_alpha_lab.research_resolution_store import load_resolution_review_with_psycopg
        return self._call(load_resolution_review_with_psycopg, review_id=review_id)

    def resolution_worklist(self, **configuration):
        from polymarket_alpha_lab.research_resolution_queue_store import load_resolution_worklist_with_psycopg
        return self._call(load_resolution_worklist_with_psycopg, **configuration)

    def collect_resolution_candidates(self, **configuration):
        from polymarket_alpha_lab.research_resolution_poll import collect_resolution_candidates_with_psycopg
        return self._call(collect_resolution_candidates_with_psycopg, **configuration)

    def evaluate(self, **configuration):
        from polymarket_alpha_lab.research_execution_psycopg import load_captured_research_evaluation_with_psycopg
        return self._call(load_captured_research_evaluation_with_psycopg, **configuration)

    def evaluate_paper(self, *, scenarios, **configuration):
        from polymarket_alpha_lab.research_paper_evaluation import evaluate_research_paper_with_psycopg
        return self._call(evaluate_research_paper_with_psycopg, scenarios=scenarios, **configuration)
