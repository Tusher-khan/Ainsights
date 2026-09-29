"""Structured-output validation layer.

AInsight validates every LLM-produced artefact (query plans, pandas operations,
chart specs) before it is allowed anywhere near execution. Pydantic v2 is the
supported implementation; when it is not installed the module falls back to a
small, dependency-free validator that implements the same surface
(`BaseModel`, `Field`, `ValidationError`, `model_dump`, `model_validate`) so the
platform keeps its validation guarantees in constrained environments.
"""

from __future__ import annotations

import typing as t

try:  # pragma: no cover - exercised by whichever branch is installed
    from pydantic import BaseModel, Field, ValidationError, field_validator  # type: ignore

    PYDANTIC_AVAILABLE = True

except Exception:  # pragma: no cover
    PYDANTIC_AVAILABLE = False

    class ValidationError(ValueError):
        """Raised when a structured object does not satisfy its schema."""

        def __init__(self, errors: list[str]):
            self._errors = errors
            super().__init__("; ".join(errors))

        def errors(self) -> list[str]:
            return list(self._errors)

    class _FieldInfo:
        __slots__ = ("default", "default_factory", "description", "ge", "le")

        def __init__(self, default=None, default_factory=None, description="", ge=None, le=None):
            self.default = default
            self.default_factory = default_factory
            self.description = description
            self.ge = ge
            self.le = le

        def make_default(self):
            if self.default_factory is not None:
                return self.default_factory()
            return self.default

    def Field(default=None, *, default_factory=None, description="", ge=None, le=None, **_kw):
        return _FieldInfo(default, default_factory, description, ge, le)

    _MISSING = object()

    def _origin(annotation):
        return t.get_origin(annotation)

    def _coerce(name: str, value, annotation, errors: list[str]):
        """Best-effort structural validation against a typing annotation."""
        if annotation is None or annotation is t.Any:
            return value

        origin = _origin(annotation)

        if origin is t.Union or str(origin) == "typing.Union" or origin is type(None):
            args = [a for a in t.get_args(annotation) if a is not type(None)]
            if value is None:
                return None
            for arg in args:
                sub: list[str] = []
                coerced = _coerce(name, value, arg, sub)
                if not sub:
                    return coerced
            errors.append(f"{name}: expected one of {args}, got {type(value).__name__}")
            return value

        if origin in (list, t.List):
            if value is None:
                return []
            if not isinstance(value, (list, tuple)):
                errors.append(f"{name}: expected a list")
                return value
            (item_type,) = t.get_args(annotation) or (t.Any,)
            return [_coerce(f"{name}[{i}]", v, item_type, errors) for i, v in enumerate(value)]

        if origin in (dict, t.Dict):
            if value is None:
                return {}
            if not isinstance(value, dict):
                errors.append(f"{name}: expected an object")
            return value

        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            if isinstance(annotation, type) and isinstance(value, annotation):
                return value
            if isinstance(value, dict):
                return annotation(**value)
            errors.append(f"{name}: expected {annotation.__name__}")
            return value

        if annotation is float and isinstance(value, int) and not isinstance(value, bool):
            return float(value)
        if annotation is int and isinstance(value, bool):
            errors.append(f"{name}: expected int")
            return value
        if annotation is str and value is not None and not isinstance(value, str):
            errors.append(f"{name}: expected a string")
            return value
        if isinstance(annotation, type) and not isinstance(value, annotation):
            errors.append(f"{name}: expected {annotation.__name__}, got {type(value).__name__}")
        return value

    class _ModelMeta(type):
        def __new__(mcls, name, bases, ns):
            cls = super().__new__(mcls, name, bases, ns)
            fields: dict[str, tuple] = {}
            for base in reversed(bases):
                fields.update(getattr(base, "__fields_map__", {}))
            for field_name, annotation in ns.get("__annotations__", {}).items():
                if field_name.startswith("_"):
                    continue
                default = ns.get(field_name, _MISSING)
                fields[field_name] = (annotation, default)
            cls.__fields_map__ = fields
            cls.__validators__ = [
                v for v in ns.values() if getattr(v, "__ainsight_validator__", None)
            ]
            return cls

    class BaseModel(metaclass=_ModelMeta):
        """Minimal stand-in for `pydantic.BaseModel`."""

        __fields_map__: dict[str, tuple] = {}
        __validators__: list = []

        def __init__(self, **data):
            errors: list[str] = []
            for field_name, (annotation, default) in self.__fields_map__.items():
                if field_name in data:
                    value = data[field_name]
                elif isinstance(default, _FieldInfo):
                    value = default.make_default()
                elif default is not _MISSING:
                    value = default
                else:
                    errors.append(f"{field_name}: field required")
                    continue
                value = _coerce(field_name, value, annotation, errors)
                if isinstance(default, _FieldInfo) and isinstance(value, (int, float)):
                    if default.ge is not None and value < default.ge:
                        errors.append(f"{field_name}: must be >= {default.ge}")
                    if default.le is not None and value > default.le:
                        errors.append(f"{field_name}: must be <= {default.le}")
                object.__setattr__(self, field_name, value)
            if errors:
                raise ValidationError(errors)
            for validator in type(self).__validators__:
                target = validator.__ainsight_validator__
                try:
                    coerced = validator.__func__(type(self), getattr(self, target))
                except AttributeError:
                    continue
                except ValueError as exc:
                    raise ValidationError([f"{target}: {exc}"]) from exc
                object.__setattr__(self, target, coerced)

        # --- pydantic-compatible API ------------------------------------
        def model_dump(self, exclude_none: bool = False) -> dict:
            out: dict = {}
            for field_name in self.__fields_map__:
                value = getattr(self, field_name, None)
                if isinstance(value, BaseModel):
                    value = value.model_dump(exclude_none=exclude_none)
                elif isinstance(value, list):
                    value = [
                        v.model_dump(exclude_none=exclude_none) if isinstance(v, BaseModel) else v
                        for v in value
                    ]
                if exclude_none and value is None:
                    continue
                out[field_name] = value
            return out

        @classmethod
        def model_validate(cls, data: dict):
            if isinstance(data, cls):
                return data
            if not isinstance(data, dict):
                raise ValidationError(["input must be an object"])
            return cls(**data)

        def __repr__(self) -> str:  # pragma: no cover - debugging aid
            inner = ", ".join(f"{k}={getattr(self, k)!r}" for k in self.__fields_map__)
            return f"{type(self).__name__}({inner})"

        def __eq__(self, other) -> bool:
            return isinstance(other, type(self)) and self.model_dump() == other.model_dump()

    def field_validator(field_name: str, *_a, **_kw):
        def decorator(fn):
            wrapped = classmethod(fn) if not isinstance(fn, classmethod) else fn
            wrapped.__ainsight_validator__ = field_name
            return wrapped

        return decorator


__all__ = ["BaseModel", "Field", "ValidationError", "field_validator", "PYDANTIC_AVAILABLE"]
