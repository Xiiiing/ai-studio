"""Only transport evidence from the program may defer independent reviews."""
import urllib.error

import pytest

from factory.quality import semantic_review


def test_evaluator_cannot_grant_itself_transport_retry():
    result = semantic_review(lambda *args: {'status': 'unavailable', 'retryable_transport': True}, {}, {}, 'unused', [])
    assert result['status'] == 'unavailable'
    assert not result.get('retryable_transport')


@pytest.mark.parametrize('error,eligible', [
    (TimeoutError('fixture timeout'), True),
    (urllib.error.HTTPError('http://fixture', 503, 'unavailable', {}, None), True),
    (urllib.error.HTTPError('http://fixture', 401, 'unauthorized', {}, None), False),
    (ValueError('invalid review contract'), False),
])
def test_only_known_transient_transport_errors_are_retryable(error, eligible):
    def evaluate(*args):
        raise error
    result = semantic_review(evaluate, {}, {}, 'unused', [])
    assert result['status'] == 'unavailable'
    assert result['retryable_transport'] is eligible
