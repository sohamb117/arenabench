class ArenaError(Exception):
    pass


class _KwargError(ArenaError):
    def __init__(self, message: str, **kwargs: object) -> None:
        super().__init__(message)
        self._message = message
        self._kwargs = kwargs
        self.__dict__.update(kwargs)

    @property
    def message(self) -> str:
        return self._message

    def _parts(self) -> list[str]:
        return [f"{key}={value}" for key, value in self._kwargs.items() if value is not None]

    def __str__(self) -> str:
        parts = [self._message, *self._parts()]
        return " ".join(parts)


class TransportError(_KwargError):
    pass


class ParseError(_KwargError):
    parser: str
    line: int | None
    column: int | None

    def __init__(
        self,
        message: str,
        *,
        parser: str,
        line: int | None = None,
        column: int | None = None,
    ) -> None:
        super().__init__(message, parser=parser, line=line, column=column)

    def _parts(self) -> list[str]:
        parts = [f"parser={self.parser}"]
        if self.line is not None:
            parts.append(f"line={self.line}")
            if self.column is not None:
                parts.append(f"column={self.column}")
        return parts


class LifecycleError(_KwargError):
    state: str
    event: str

    def __init__(self, message: str, *, state: str, event: str) -> None:
        super().__init__(message, state=state, event=event)

    def _parts(self) -> list[str]:
        return [f"state={self.state}", f"event={self.event}"]


class ConfigError(_KwargError):
    path: str
    field: str | None

    def __init__(self, message: str, *, path: str, field: str | None = None) -> None:
        super().__init__(message, path=path, field=field)

    def _parts(self) -> list[str]:
        parts = [f"path={self.path}"]
        if self.field is not None:
            parts.append(f"field={self.field}")
        return parts
