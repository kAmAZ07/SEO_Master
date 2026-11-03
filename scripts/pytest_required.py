"""В обязательном unit-gate skip/xfail/xpass не являются успешной проверкой."""

from contextvars import ContextVar
import socket
from typing import NoReturn

import pytest


class RequiredTests:
    def __init__(self) -> None:
        self.has_incomplete_tests = False
        self.network_patch = pytest.MonkeyPatch()

    def block_network(self) -> None:
        original_connect = socket.socket.connect
        original_socketpair = socket.socketpair
        is_internal_pair: ContextVar[bool] = ContextVar("is_internal_pair", default=False)

        def reject_network(*arguments: object, **keywords: object) -> NoReturn:
            raise RuntimeError("Сеть запрещена в backend unit-gate")

        def guarded_connect(
            connection: socket.socket,
            address: str | bytes | tuple[str, int] | tuple[str, int, int, int],
        ) -> None:
            if not is_internal_pair.get():
                reject_network()
            original_connect(connection, address)

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
        self.network_patch.setattr(socket.socket, "connect_ex", reject_network)
        self.network_patch.setattr(socket.socket, "sendto", reject_network)
        for name in (
            "getaddrinfo", "gethostbyname", "gethostbyname_ex", "gethostbyaddr", "getnameinfo",
        ):
            self.network_patch.setattr(socket, name, reject_network)
        self.network_patch.setattr(socket, "create_connection", reject_network)
        self.network_patch.setattr(socket, "socketpair", internal_socketpair)

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
