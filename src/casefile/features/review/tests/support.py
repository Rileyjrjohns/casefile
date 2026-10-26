from casefile.features.review.domain.errors import ReviewError, ReviewErrorCode
from casefile.shared.result import Err, Result


def error_code(result: Result[object, ReviewError]) -> ReviewErrorCode:
    """Return the business error code, failing the test when the result succeeded."""
    assert isinstance(result, Err), result
    return result.error.code
