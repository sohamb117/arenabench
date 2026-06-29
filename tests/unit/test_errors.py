from common.errors import ArenaError, ConfigError, LifecycleError, ParseError, TransportError


def test_errors_smoke() -> None:
    errors = [
        TransportError("bad transport"),
        ParseError("bad", parser="json", line=10, column=3),
        LifecycleError("bad transition", state="IN_MATCH", event="boot_ack"),
        ConfigError("bad", path="match.json", field=None),
    ]

    for error in errors:
        assert isinstance(error, ArenaError)

    parse_error = ParseError("bad", parser="json", line=10, column=3)
    assert "parser=json" in str(parse_error)
    assert "line=10" in str(parse_error)
    assert "column=3" in str(parse_error)

    lifecycle_error = LifecycleError("bad transition", state="IN_MATCH", event="boot_ack")
    assert "state=IN_MATCH" in str(lifecycle_error)
    assert "event=boot_ack" in str(lifecycle_error)

    config_error = ConfigError("bad", path="match.json", field=None)
    assert "path=match.json" in str(config_error)
    assert "field=None" not in str(config_error)

    try:
        raise TransportError("bad transport")
    except ArenaError as caught:
        assert isinstance(caught, TransportError)


def test_transport_error_str_includes_kwargs() -> None:
    error = TransportError("bad transport", transport="vsock")
    assert str(error) == "bad transport transport=vsock"
