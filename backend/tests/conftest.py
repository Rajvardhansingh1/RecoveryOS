import pytest
from helpers import ScriptedLLM, good_replies

from app.adapters.github import _FIXTURES, MockGitHubAdapter
from app.core.executor import Executor
from app.core.retry import RetryPolicy
from app.db.store import Store, make_engine


@pytest.fixture
def store():
    s = Store(make_engine("sqlite+pysqlite:///:memory:"))
    s.create_schema()
    yield s
    s.engine.dispose()


@pytest.fixture
def adapter(store):
    return MockGitHubAdapter(store)


def make_executor(store, adapter, llm=None, max_attempts=3):
    return Executor(
        store,
        adapter,
        lambda cap: llm,
        policy=RetryPolicy(max_attempts=max_attempts, base_delay=0.0),
        sleep=lambda s: None,
    )


@pytest.fixture
def executor_factory(store, adapter):
    def build(llm=None, max_attempts=3):
        return make_executor(store, adapter, llm, max_attempts)

    return build


@pytest.fixture
def good_llm():
    return ScriptedLLM(good_replies(_FIXTURES["demo/acme-api"]["commits"]))
