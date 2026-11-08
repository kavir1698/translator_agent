"""Live progress display for the translation commands."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
)

from tarjoman.pipeline.translate_pass import ProgressCallback, ProgressEvent

from .ui import err_console


class _Reporter:
    def __init__(self, progress: Progress) -> None:
        self._progress = progress
        self._task: TaskID | None = None

    def __call__(self, event: ProgressEvent) -> None:
        if event.kind == "start":
            self._start(event)
            return
        if self._task is None:
            return
        if event.kind == "chapter_start":
            self._describe(
                f"chapter {_position(event.chapter_position, event.chapters_total)}"
                f" · {_phase(event.phase)}"
            )
        elif event.kind == "phase":
            self._describe(f"chapter {event.chapter_idx} · {_phase(event.phase)}")
        elif event.kind == "window_done":
            self._progress.update(
                self._task,
                advance=1,
                description=(
                    f"chapter {event.chapter_idx} · {event.segments_done} segments · "
                    f"${event.cost_usd:.4f}"
                ),
            )
        elif event.kind == "chapter_done":
            self._progress.update(
                self._task,
                description=(
                    f"chapters {event.chapters_done}/{event.chapters_total} · "
                    f"{event.segments_done} segments · ${event.cost_usd:.4f}"
                ),
            )
        elif event.kind == "finish":
            label = "paused" if event.status == "paused" else "done"
            self._describe(
                f"{label} · {event.segments_done} segments · ${event.cost_usd:.4f}"
            )

    def _start(self, event: ProgressEvent) -> None:
        self._task = self._progress.add_task(
            f"translating · {event.chapters_total} chapters · {event.windows_total} windows",
            total=event.windows_total or event.chapters_total or 1,
        )

    def _describe(self, text: str) -> None:
        assert self._task is not None
        self._progress.update(self._task, description=text)


def _position(position: int | None, total: int) -> str:
    if position is None or total <= 0:
        return "?"
    return f"{position}/{total}"


def _phase(phase: str | None) -> str:
    return phase or "drafting"


@contextmanager
def translation_progress(
    *, enabled: bool | None = None
) -> Iterator[ProgressCallback]:
    """Yield a progress callback; auto-disables when stderr is not a terminal."""
    disable = not err_console.is_terminal if enabled is None else not enabled
    progress = Progress(
        SpinnerColumn(style="cyan"),
        TextColumn("[bold]{task.description}"),
        BarColumn(bar_width=None),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=err_console,
        transient=False,
        disable=disable,
    )
    reporter = _Reporter(progress)
    with progress:
        yield reporter
