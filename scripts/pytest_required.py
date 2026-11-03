"""В обязательном unit-gate skip/xfail/xpass не являются успешной проверкой."""

from contextvars import ContextVar
import errno
import importlib
import socket
from typing import NoReturn
import weakref

import pytest

SocketAddress = str | bytes | tuple[str, int] | tuple[str, int, int, int]
Endpoint = tuple[int, str, int]


def loopback_endpoint(family: int, address: SocketAddress) -> Endpoint | None:
    if not isinstance(address, tuple):
        return None
    host, port = address[:2]
    if family == socket.AF_INET and len(address) == 2 and host == "127.0.0.1":
        return family, host, port
    if (
        family == socket.AF_INET6 and host == "::1"
        and (len(address) == 2 or address[2:] == (0, 0))
    ):
        return family, host, port
    return None


class RequiredTests:
    def __init__(self) -> None:
        self.has_incomplete_tests = False
        self.network_patch = pytest.MonkeyPatch()

    def block_network(self) -> None:
        original_connect = socket.socket.connect
        original_connect_ex = socket.socket.connect_ex
        original_bind = socket.socket.bind
        original_listen = socket.socket.listen
        original_socketpair = socket.socketpair
        is_internal_pair: ContextVar[bool] = ContextVar("is_internal_pair", default=False)
        bound: dict[Endpoint, weakref.ReferenceType[socket.socket]] = {}
        listening: set[Endpoint] = set()

        def reject_network(*arguments: object, **keywords: object) -> NoReturn:
            raise RuntimeError("Сеть запрещена в backend unit-gate")

        def require_tcp_socket(connection: socket.socket) -> None:
            if (
                connection.family not in (socket.AF_INET, socket.AF_INET6)
                or connection.type != socket.SOCK_STREAM
                or connection.proto not in (0, socket.IPPROTO_TCP)
            ):
                reject_network()

        def guarded_bind(connection: socket.socket, address: SocketAddress) -> None:
            require_tcp_socket(connection)
            endpoint = loopback_endpoint(connection.family, address)
            if endpoint is None or endpoint[2] != 0:
                reject_network()
            original_bind(connection, address)
            allocated = loopback_endpoint(connection.family, connection.getsockname())
            if allocated is None:
                reject_network()
            bound[allocated] = weakref.ref(connection)
            listening.discard(allocated)

        def guarded_listen(connection: socket.socket, backlog: int = 128) -> None:
            require_tcp_socket(connection)
            endpoint = loopback_endpoint(connection.family, connection.getsockname())
            if endpoint is None or endpoint not in bound or bound[endpoint]() is not connection:
                reject_network()
            original_listen(connection, backlog)
            listening.add(endpoint)

        def is_owned_listener(connection: socket.socket, address: SocketAddress) -> bool:
            require_tcp_socket(connection)
            endpoint = loopback_endpoint(connection.family, address)
            if endpoint is None or endpoint not in bound:
                reject_network()
            owner = bound[endpoint]()
            if owner is not None:
                require_tcp_socket(owner)
            return (
                owner is not None and owner.fileno() != -1 and endpoint in listening
                and bool(owner.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN))
            )

        def guarded_connect(
            connection: socket.socket, address: SocketAddress,
        ) -> None:
            require_tcp_socket(connection)
            if not is_internal_pair.get() and not is_owned_listener(connection, address):
                # Never contact a port after its fixture listener was closed.
                raise ConnectionRefusedError(errno.ECONNREFUSED, "Fixture listener is closed")
            original_connect(connection, address)
        def guarded_connect_ex(connection: socket.socket, address: SocketAddress) -> int:
            require_tcp_socket(connection)
            if not is_internal_pair.get() and not is_owned_listener(connection, address):
                return errno.ECONNREFUSED
            return original_connect_ex(connection, address)

        def internal_socketpair(
            family: int | None = None, type: int = socket.SOCK_STREAM, proto: int = 0,
        ) -> tuple[socket.socket, socket.socket]:
            # Windows asyncio uses TCP socketpair; only this synchronous stdlib
            # operation may connect. ContextVar does not permit other threads.
            token = is_internal_pair.set(True)
            try:
                if family is None:
                    return original_socketpair()
                return original_socketpair(family, type, proto)
            finally:
                is_internal_pair.reset(token)

        self.network_patch.setattr(socket.socket, "connect", guarded_connect)
        self.network_patch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
        self.network_patch.setattr(socket.socket, "bind", guarded_bind)
        self.network_patch.setattr(socket.socket, "listen", guarded_listen)
        self.network_patch.setattr(socket.socket, "sendto", reject_network)
        if hasattr(socket.socket, "sendmsg"):
            self.network_patch.setattr(socket.socket, "sendmsg", reject_network)
        for name in (
            "getaddrinfo", "gethostbyname", "gethostbyname_ex", "gethostbyaddr", "getnameinfo",
        ):
            self.network_patch.setattr(socket, name, reject_network)
        self.network_patch.setattr(socket, "create_connection", reject_network)
        self.network_patch.setattr(socket, "socketpair", internal_socketpair)
        # libpq can bypass Python sockets; close native DBAPI entry points as well.
        # Some faithful profiles deliberately do not install these optional drivers.
        for name, functions in (
            ("psycopg2", ("connect",)), ("psycopg", ("connect",)),
            ("asyncpg", ("connect", "create_pool")),
        ):
            try:
                driver = importlib.import_module(name)
            except ModuleNotFoundError as error:
                if error.name != name:
                    raise
                continue
            for function in functions:
                self.network_patch.setattr(driver, function, reject_network)

    def pytest_collectreport(self, report: pytest.CollectReport) -> None:
        if report.skipped:
            self.has_incomplete_tests = True

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        if report.skipped or hasattr(report, "wasxfail"):
            self.has_incomplete_tests = True

    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        if self.has_incomplete_tests:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED


@pytest.hookimpl(tryfirst=True)
def pytest_load_initial_conftests(early_config: pytest.Config) -> None:
    policy = RequiredTests()
    policy.block_network()
    early_config.pluginmanager.register(policy, "required-tests-policy")
