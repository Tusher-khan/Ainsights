#!/usr/bin/env python3
"""Run the AInsight test suite without pytest installed.

`pytest` is the supported test runner (see requirements.txt):

    pytest -q

This script exists so the suite can also run in constrained environments
where pytest cannot be installed. It implements the small subset of the
pytest API the tests use — fixtures with session and function scope,
`parametrize`, `raises`, `approx`, `tmp_path` and `tmp_path_factory` — and
reports results in the same shape. It is a fallback, not a replacement.

    python tests/run_tests.py            # run everything
    python tests/run_tests.py test_rag   # run one module
"""

from __future__ import annotations

import importlib.util
import inspect
import shutil
import sys
import tempfile
import traceback
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
sys.path.insert(0, str(ROOT))


# --------------------------------------------------------------------------
# A minimal stand-in for the pytest API
# --------------------------------------------------------------------------


class _ApproxNumber:
    def __init__(self, expected, rel=1e-6, abs_=1e-9):
        self.expected, self.rel, self.abs = expected, rel, abs_

    def __eq__(self, other):
        try:
            return abs(float(other) - float(self.expected)) <= max(
                self.abs, abs(float(self.expected)) * self.rel
            )
        except (TypeError, ValueError):
            return False

    def __repr__(self):
        return f"approx({self.expected})"


class _Raises:
    def __init__(self, expected):
        self.expected = expected
        self.value = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            raise AssertionError(f"DID NOT RAISE {self.expected}")
        if issubclass(exc_type, self.expected):
            self.value = exc
            return True
        return False


class _TmpPathFactory:
    def __init__(self, root: Path):
        self.root = root
        self._counter = 0

    def mktemp(self, name: str) -> Path:
        self._counter += 1
        path = self.root / f"{name}{self._counter}"
        path.mkdir(parents=True, exist_ok=True)
        return path


def _build_pytest_module(registry: dict) -> types.ModuleType:
    module = types.ModuleType("pytest")

    def fixture(func=None, *, scope="function", autouse=False, **_kw):
        def decorate(fn):
            registry[fn.__name__] = {"func": fn, "scope": scope, "autouse": autouse}
            fn.__ainsight_fixture__ = True
            return fn

        return decorate(func) if func else decorate

    def parametrize(argnames, argvalues, **_kw):
        names = [n.strip() for n in argnames.split(",")] if isinstance(argnames, str) else list(argnames)

        def decorate(fn):
            fn.__ainsight_params__ = (names, list(argvalues))
            return fn

        return decorate

    mark = types.SimpleNamespace(parametrize=parametrize, skip=lambda *a, **k: (lambda f: f))

    module.fixture = fixture
    module.mark = mark
    module.raises = lambda expected, **_kw: _Raises(expected)
    module.approx = lambda expected, rel=1e-6, abs=1e-9: _ApproxNumber(expected, rel, abs)
    module.skip = lambda reason="": (_ for _ in ()).throw(_Skip(reason))
    return module


class _Skip(Exception):
    pass


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------


def main(selector: str | None = None) -> int:
    registry: dict = {}
    sys.modules["pytest"] = _build_pytest_module(registry)

    workspace = Path(tempfile.mkdtemp(prefix="ainsight-tests-"))
    factory = _TmpPathFactory(workspace)
    session_cache: dict = {"tmp_path_factory": factory}

    def load(path: Path):
        spec = importlib.util.spec_from_file_location(path.stem, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[path.stem] = module
        spec.loader.exec_module(module)
        return module

    conftest = TESTS / "conftest.py"
    if conftest.exists():
        load(conftest)

    # Session-scoped autouse fixtures run first.
    generators: list = []

    def resolve(name: str, function_cache: dict):
        if name == "tmp_path":
            return factory.mktemp("tmp")
        if name == "tmp_path_factory":
            return factory
        if name in function_cache:
            return function_cache[name]
        if name in session_cache:
            return session_cache[name]
        entry = registry.get(name)
        if entry is None:
            raise AssertionError(f"fixture '{name}' not found")
        kwargs = {
            p: resolve(p, function_cache)
            for p in inspect.signature(entry["func"]).parameters
        }
        value = entry["func"](**kwargs)
        if inspect.isgenerator(value):
            generators.append(value)
            value = next(value)
        (session_cache if entry["scope"] == "session" else function_cache)[name] = value
        return value

    for name, entry in list(registry.items()):
        if entry["autouse"] and entry["scope"] == "session":
            resolve(name, {})

    modules = sorted(p for p in TESTS.glob("test_*.py") if not selector or selector in p.stem)
    passed = failed = 0
    failures: list[tuple[str, str]] = []

    for path in modules:
        module = load(path)
        for attr in sorted(dir(module)):
            if not attr.startswith("test_"):
                continue
            function = getattr(module, attr)
            if not callable(function) or getattr(function, "__ainsight_fixture__", False):
                continue
            params = getattr(function, "__ainsight_params__", None)
            cases = [dict(zip(params[0], v if isinstance(v, (tuple, list)) else (v,))) for v in params[1]] if params else [{}]

            for case in cases:
                label = f"{path.stem}::{attr}" + (f"[{list(case.values())}]" if case else "")
                function_cache: dict = {}
                try:
                    kwargs = dict(case)
                    for parameter in inspect.signature(function).parameters:
                        if parameter not in kwargs:
                            kwargs[parameter] = resolve(parameter, function_cache)
                    function(**kwargs)
                    passed += 1
                    print(".", end="", flush=True)
                except _Skip:
                    print("s", end="", flush=True)
                except Exception:
                    failed += 1
                    failures.append((label, traceback.format_exc()))
                    print("F", end="", flush=True)

    for generator in reversed(generators):
        try:
            next(generator)
        except StopIteration:
            pass
        except Exception:
            pass

    print("\n")
    for label, tb in failures:
        print("=" * 70)
        print("FAILED", label)
        print(tb)
    print(f"{passed} passed, {failed} failed")
    shutil.rmtree(workspace, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
