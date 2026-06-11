from __future__ import annotations

from typing import TypedDict


class ModelSource(TypedDict):
    id: str
    label: str
    namespace: str


DEFAULT_SOURCE_ID = "library"
MODEL_SOURCES: tuple[ModelSource, ...] = (
    {"id": "library", "label": "Official", "namespace": "library"},
    {"id": "mlx-community", "label": "MLX Community", "namespace": "mlx-community"},
)


def model_sources() -> list[ModelSource]:
    return [dict(source) for source in MODEL_SOURCES]


def source_by_id(source_id: str | None) -> ModelSource:
    wanted = source_id or DEFAULT_SOURCE_ID
    for source in MODEL_SOURCES:
        if source["id"] == wanted:
            return dict(source)
    raise ValueError(f"Unknown source: {wanted}")
