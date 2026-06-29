from common.ids import make_agent_slot, make_match_id, make_pid, make_request_id

_MAX_AGENT_SLOT = 15
_REQUEST_ID_HEX_LEN = 32
_MAX_PID = 1 << 22


def test_make_match_id_returns_input_for_valid_id() -> None:
    assert make_match_id("demo-1v1") == "demo-1v1"


def test_make_match_id_rejects_empty_string() -> None:
    try:
        make_match_id("")
    except ValueError as exc:
        assert str(exc) == "invalid match_id: ''"
    else:
        raise AssertionError("expected ValueError")


def test_make_match_id_rejects_spaces() -> None:
    try:
        make_match_id("has spaces")
    except ValueError as exc:
        assert str(exc) == "invalid match_id: 'has spaces'"
    else:
        raise AssertionError("expected ValueError")


def test_make_match_id_rejects_too_long_values() -> None:
    try:
        make_match_id("a" * 65)
    except ValueError as exc:
        assert str(exc) == f"invalid match_id: {'a' * 65!r}"
    else:
        raise AssertionError("expected ValueError")


def test_make_agent_slot_accepts_bounds() -> None:
    assert make_agent_slot(0) == 0
    assert make_agent_slot(_MAX_AGENT_SLOT) == _MAX_AGENT_SLOT


def test_make_agent_slot_rejects_out_of_bounds() -> None:
    try:
        make_agent_slot(-1)
    except ValueError as exc:
        assert str(exc) == "invalid agent_slot: -1"
    else:
        raise AssertionError("expected ValueError")

    try:
        make_agent_slot(16)
    except ValueError as exc:
        assert str(exc) == "invalid agent_slot: 16"
    else:
        raise AssertionError("expected ValueError")


def test_make_request_id_returns_unique_hex_strings() -> None:
    first = make_request_id()
    second = make_request_id()

    assert len(first) == _REQUEST_ID_HEX_LEN
    assert len(second) == _REQUEST_ID_HEX_LEN
    assert first != second
    assert int(first, 16) >= 0
    assert int(second, 16) >= 0


def test_make_pid_accepts_positive_pid() -> None:
    assert make_pid(1) == 1


def test_make_pid_rejects_invalid_values() -> None:
    try:
        make_pid(0)
    except ValueError as exc:
        assert str(exc) == "invalid pid: 0"
    else:
        raise AssertionError("expected ValueError")

    try:
        make_pid(_MAX_PID + 1)
    except ValueError as exc:
        assert str(exc) == f"invalid pid: {(2**22) + 1}"
    else:
        raise AssertionError("expected ValueError")
