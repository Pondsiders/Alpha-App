"""topics.py — Topic discovery and context loading.

Topics are directories in JE_NE_SAIS_QUOI/topics/, each containing:
    context.md          Static context (always loaded)
    context.py          Optional dynamic context module (hot-loaded)

The scanner walks the topics directory on startup and caches what it finds.
Dynamic modules are loaded via importlib and re-imported when their mtime
changes — no restart required to pick up new or modified topics.

Usage:
    from alpha_app.topics import TopicRegistry

    registry = TopicRegistry("/Pondside/Alpha-Home/Alpha/topics")
    registry.scan()  # Find all topics

    # Get context for a topic (static + dynamic)
    context = await registry.get_context("alpha-app")

    # List available topics
    names = registry.list_topics()
"""

from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

import logfire


@dataclass
class Topic:
    """A single topic with static markdown and optional dynamic module."""

    name: str
    directory: Path
    md_path: Path
    py_path: Path | None = None
    _module: ModuleType | None = field(default=None, repr=False)
    _module_mtime: float = 0.0
    _md_cache: str | None = field(default=None, repr=False)
    _md_mtime: float = 0.0

    def get_static_context(self) -> str:
        """Read the static context.md, caching by mtime."""
        try:
            current_mtime = self.md_path.stat().st_mtime
            if self._md_cache is not None and current_mtime == self._md_mtime:
                return self._md_cache
            self._md_cache = self.md_path.read_text(encoding="utf-8")
            self._md_mtime = current_mtime
            return self._md_cache
        except Exception as e:
            logfire.warn(f"topics: failed to read {self.md_path}: {e}")
            return ""

    def get_dynamic_context(self) -> str:
        """Load and call the dynamic context.py module, hot-reloading on change."""
        if self.py_path is None or not self.py_path.exists():
            return ""

        try:
            current_mtime = self.py_path.stat().st_mtime

            # Hot-reload if file changed or not yet loaded
            if self._module is None or current_mtime != self._module_mtime:
                spec = importlib.util.spec_from_file_location(
                    f"topic_{self.name}_context",
                    self.py_path,
                )
                if spec is None or spec.loader is None:
                    return ""
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                self._module = module
                self._module_mtime = current_mtime
                logfire.info(
                    f"topics: loaded dynamic module for '{self.name}'",
                    topic=self.name,
                )

            # Call the context() function
            context_fn = getattr(self._module, "context", None)
            if context_fn is None:
                logfire.warn(
                    f"topics: {self.py_path} has no context() function",
                    topic=self.name,
                )
                return ""

            result = context_fn()
            return result if isinstance(result, str) else str(result)

        except Exception as e:
            logfire.warn(
                f"topics: error in dynamic context for '{self.name}': {e}",
                topic=self.name,
            )
            return ""

    def get_context(self) -> str:
        """Get full context: static + dynamic."""
        static = self.get_static_context()
        dynamic = self.get_dynamic_context()

        if dynamic:
            return f"{static}\n\n{dynamic}"
        return static


class TopicRegistry:
    """Discovers and manages topics from a directory.

    Scans JE_NE_SAIS_QUOI/topics/ for subdirectories containing context.md.
    Each subdirectory name becomes a topic name. Hot-reloads dynamic modules
    when their source files change.
    """

    def __init__(self, topics_dir: str | Path):
        self._dir = Path(topics_dir)
        self._topics: dict[str, Topic] = {}

    def scan(self) -> list[str]:
        """Walk the topics directory and register all topics.

        Returns list of discovered topic names.
        """
        if not self._dir.is_dir():
            logfire.warn(f"topics: directory not found: {self._dir}")
            return []

        discovered = []
        for entry in sorted(self._dir.iterdir()):
            if not entry.is_dir():
                continue
            if entry.name.startswith(".") or entry.name.startswith("_"):
                continue

            md_path = entry / "context.md"
            if not md_path.exists():
                continue  # Not a topic without context.md

            py_path = entry / "context.py"
            if not py_path.exists():
                py_path = None

            # Register or update
            if entry.name in self._topics:
                # Update paths in case structure changed
                topic = self._topics[entry.name]
                topic.py_path = py_path
            else:
                self._topics[entry.name] = Topic(
                    name=entry.name,
                    directory=entry,
                    md_path=md_path,
                    py_path=py_path,
                )

            discovered.append(entry.name)

        # Remove topics whose directories were deleted
        for name in list(self._topics.keys()):
            if name not in discovered:
                del self._topics[name]

        logfire.info(
            f"topics: scanned {len(discovered)} topic(s): {', '.join(discovered)}",
        )
        return discovered

    def rescan(self) -> list[str]:
        """Re-scan for new or removed topics. Alias for scan()."""
        return self.scan()

    def list_topics(self) -> list[str]:
        """Return sorted list of available topic names."""
        # Re-scan to pick up newly created topics
        if self._dir.is_dir():
            self.scan()
        return sorted(self._topics.keys())

    def get_context(self, name: str) -> str | None:
        """Get context for a topic by name.

        Returns the combined static + dynamic context string,
        or None if the topic doesn't exist.

        Hot-reloads dynamic modules if their source has changed.
        Re-scans the directory to pick up newly created topics.
        """
        # Re-scan to pick up newly created topics
        if name not in self._topics and self._dir.is_dir():
            self.scan()

        topic = self._topics.get(name)
        if topic is None:
            return None

        with logfire.span("topics.get_context", topic=name):
            return topic.get_context()

    def has_topic(self, name: str) -> bool:
        """Check if a topic exists."""
        if name not in self._topics:
            self.scan()
        return name in self._topics
