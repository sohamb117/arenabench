import os
import pathlib
import pwd
import socket
import sys
from datetime import UTC, datetime
from typing import IO

from pydantic import ValidationError

from common.errors import TransportError
from common.protocol import (
    BootAck,
    BootAckResponse,
    Envelope,
    Kill0,
    Kill0Response,
    ProcList,
    ProcListResponse,
    parse_envelope,
    serialize_envelope,
)

_CID = {"value": 0}
_STDIO_FLAG = "--stdio"


def kill0_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (PermissionError, ProcessLookupError):
        return False


def proc_pids_for_user(user: str) -> list[int]:
    try:
        uid = pwd.getpwnam(user).pw_uid
    except KeyError:
        return []
    pids: list[int] = []
    for proc in pathlib.Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if not (proc / "cmdline").read_bytes():
                continue
            lines = (proc / "status").read_text().splitlines()
            if any(line.startswith("Uid:") and int(line.split()[2]) == uid for line in lines):
                pids.append(int(proc.name))
        except OSError:
            pass
    return sorted(pids)


def boot_info() -> tuple[str, float]:
    return os.uname().release, float(pathlib.Path("/proc/uptime").read_text().split()[0])


def handle_request(env: Envelope) -> Envelope | None:
    if isinstance(env.data, Kill0):
        data = Kill0Response(
            request_id=env.data.request_id,
            pid=env.data.pid,
            alive=kill0_alive(env.data.pid),
        )
    elif isinstance(env.data, ProcList):
        data = ProcListResponse(
            request_id=env.data.request_id,
            user=env.data.user,
            pids=proc_pids_for_user(env.data.user),
        )
    elif isinstance(env.data, BootAck):
        kernel, uptime_s = boot_info()
        data = BootAckResponse(
            request_id=env.data.request_id,
            kernel=kernel,
            uptime_s=uptime_s,
            cid=_CID["value"],
        )
    else:
        return None
    return Envelope(
        v=1,
        ts=datetime.now(UTC),
        seq=env.seq,
        src="guest_probe",
        dst=env.src,
        kind=data.kind,
        data=data,
    )


def _serve_conn(conn: socket.socket) -> None:
    buf = ""
    try:
        while chunk := conn.recv(4096):
            buf += chunk.decode()
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                if line:
                    resp = handle_request(parse_envelope(line))
                    if resp is not None:
                        conn.sendall((serialize_envelope(resp) + "\n").encode())
    except (TransportError, ValidationError, OSError, ValueError):
        return


def serve(listener: socket.socket, our_cid: int = 0) -> None:
    _CID["value"] = our_cid
    try:
        if not listener.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN):
            _serve_conn(listener)
            return
    except OSError:
        _serve_conn(listener)
        return
    while True:
        try:
            conn, _ = listener.accept()
        except OSError:
            return
        with conn:
            _serve_conn(conn)


def serve_stdio(
    stdin: IO[str] | None = None,
    stdout: IO[str] | None = None,
    our_cid: int = 0,
) -> None:
    """Serve JSONL probe requests over stdin/stdout (used by SSH transport).

    SshOrchestratorServer runs `python3 -m vm.guest_probe --stdio` over SSH;
    the SSH stdin/stdout pipes are the wire, mirroring StdioTransport on the
    harness side.
    """
    _CID["value"] = our_cid
    src_in = stdin if stdin is not None else sys.stdin
    src_out = stdout if stdout is not None else sys.stdout
    try:
        for raw in src_in:
            line = raw.rstrip("\n")
            if not line:
                continue
            try:
                resp = handle_request(parse_envelope(line))
            except (TransportError, ValidationError, ValueError):
                continue
            if resp is not None:
                src_out.write(serialize_envelope(resp) + "\n")
                src_out.flush()
    except (KeyboardInterrupt, BrokenPipeError):
        return


def main(argv: list[str] | None = None) -> None:
    args = list(argv if argv is not None else sys.argv[1:])
    if _STDIO_FLAG in args:
        serve_stdio()
        return
    if hasattr(socket, "AF_VSOCK"):
        listener = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
        listener.bind((getattr(socket, "VMADDR_CID_ANY", 2), 9999))
    else:
        path = os.environ.get("ARENABENCH_PROBE_SOCK", "/tmp/arenabench-probe.sock")
        pathlib.Path(path).unlink(missing_ok=True)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(path)
    listener.listen()
    serve(listener)


if __name__ == "__main__":
    main()
