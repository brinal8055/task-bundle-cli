from pathlib import Path
from typing import Any

import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode

from task_bundle.errors import ErrorCode, ErrorContext, TaskBundleError


class UniqueKeySafeLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: UniqueKeySafeLoader,
    node: MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as error:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable mapping key",
                key_node.start_mark,
            ) from error
        if duplicate:
            raise DuplicateKeyError(str(key), key_node.start_mark.line, key_node.start_mark.column)
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


class DuplicateKeyError(yaml.YAMLError):
    def __init__(self, key: str, line: int, column: int) -> None:
        super().__init__(f"duplicate key {key!r}")
        self.key = key
        self.line = line
        self.column = column


def load_yaml_mapping(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise TaskBundleError(
            ErrorCode.BUNDLE_NOT_FOUND,
            "Task configuration could not be read.",
            ErrorContext(
                phase="bundle-yaml",
                expected="A readable task.yaml file",
                actual=str(error),
                corrective_action="Provide a readable task.yaml in the bundle root.",
                path=Path("task.yaml"),
            ),
        ) from error

    try:
        data = yaml.load(text, Loader=UniqueKeySafeLoader)
    except DuplicateKeyError as error:
        raise TaskBundleError(
            ErrorCode.BUNDLE_DUPLICATE_KEY,
            f"Duplicate YAML key {error.key!r}.",
            ErrorContext(
                phase="bundle-yaml",
                expected="Every mapping key to occur once",
                actual=f"Duplicate at line {error.line + 1}, column {error.column + 1}",
                corrective_action="Remove the duplicate key.",
                path=Path("task.yaml"),
                details={"line": error.line + 1, "column": error.column + 1},
            ),
        ) from error
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        location = ""
        details: dict[str, object] | None = None
        if mark is not None:
            location = f" at line {mark.line + 1}, column {mark.column + 1}"
            details = {"line": mark.line + 1, "column": mark.column + 1}
        raise TaskBundleError(
            ErrorCode.BUNDLE_YAML_ERROR,
            "Task YAML is invalid.",
            ErrorContext(
                phase="bundle-yaml",
                expected="Safe YAML with a mapping at its root",
                actual=f"{error}{location}",
                corrective_action="Correct the YAML syntax and use only standard safe tags.",
                path=Path("task.yaml"),
                details=details,
            ),
        ) from error

    if data is None:
        raise TaskBundleError(
            ErrorCode.BUNDLE_YAML_ERROR,
            "Task YAML is empty.",
            ErrorContext(
                phase="bundle-yaml",
                expected="A task configuration mapping",
                actual="The document contains no value",
                corrective_action="Add the required task configuration.",
                path=Path("task.yaml"),
            ),
        )
    if not isinstance(data, dict):
        raise TaskBundleError(
            ErrorCode.BUNDLE_YAML_ERROR,
            "Task YAML root must be a mapping.",
            ErrorContext(
                phase="bundle-yaml",
                expected="A mapping at the document root",
                actual=type(data).__name__,
                corrective_action="Use key-value task configuration at the document root.",
                path=Path("task.yaml"),
            ),
        )
    return data
