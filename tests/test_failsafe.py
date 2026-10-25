from app.failsafe import ErrorThrottle, FailCounter


def test_ten_failures_in_a_row_trip_once():
    counter = FailCounter()
    for _ in range(9):
        assert not counter.fail()
    assert counter.fail()
    assert counter.tripped
    assert not counter.fail()


def test_a_good_step_resets_the_count():
    counter = FailCounter()
    for _ in range(9):
        counter.fail()
    counter.ok()
    for _ in range(9):
        assert not counter.fail()
    assert not counter.tripped


def test_recovery_after_trip_is_reported_once():
    counter = FailCounter()
    for _ in range(10):
        counter.fail()
    assert counter.ok() is True
    assert counter.ok() is False
    assert not counter.tripped


def test_repeated_error_within_a_minute_is_written_once():
    throttle = ErrorThrottle()
    assert throttle.should_write("boom", 0) == (True, 0)
    assert throttle.should_write("boom", 10) == (False, 0)
    assert throttle.should_write("boom", 30) == (False, 0)
    assert throttle.should_write("boom", 61) == (True, 2)


def test_different_error_is_written_at_once():
    throttle = ErrorThrottle()
    assert throttle.should_write("boom", 0) == (True, 0)
    assert throttle.should_write("other", 1) == (True, 0)
