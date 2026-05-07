"""Generate one Markdown stub per Python module under `src/alpha/`.

Run by mkdocs-gen-files at build time. For every non-empty `.py` file
in the package, emit a virtual `reference/<dotted-path>.md` containing
one mkdocstrings directive that points at the module. Also emits a
`reference/SUMMARY.md` consumed by mkdocs-literate-nav to build the
sidebar tree.

Empty `__init__.py` files are skipped — `navigation.indexes` will use
the section's first child as the landing page instead. `__main__.py`
is always skipped (entry-point shim, not API surface).

Nothing is written to disk; mkdocs-gen-files holds the generated
files in memory and mkdocs reads them as if they existed.
"""

from pathlib import Path

import mkdocs_gen_files

src_root = Path("src")
package_root = src_root / "alpha"

nav = mkdocs_gen_files.Nav()


def is_substantive(path: Path) -> bool:
    """An __init__.py with only a module docstring isn't worth a page."""
    if path.name != "__init__.py":
        return True
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return False
    # Strip a leading docstring (single- or triple-quoted) and see if
    # anything else is left.
    lines = [line for line in text.splitlines() if line.strip()]
    in_docstring = False
    quote = None
    for line in lines:
        s = line.strip()
        if not in_docstring:
            if s.startswith(('"""', "'''")):
                quote = s[:3]
                # Single-line docstring like `"""foo"""`
                if s.count(quote) >= 2 and len(s) > 3:
                    continue
                in_docstring = True
                continue
            if s.startswith(("#",)):
                continue
            return True
        else:
            if quote in s:
                in_docstring = False
    return False


for path in sorted(package_root.rglob("*.py")):
    module_path = path.relative_to(src_root).with_suffix("")
    parts = tuple(module_path.parts)

    if parts[-1] == "__main__":
        continue

    if parts[-1] == "__init__":
        if not is_substantive(path):
            continue
        parts = parts[:-1]
        doc_path = Path(*parts) / "index.md"
    else:
        doc_path = Path(*parts).with_suffix(".md")

    if not parts:
        continue

    full_doc_path = Path("reference") / doc_path
    nav[parts] = doc_path.as_posix()

    with mkdocs_gen_files.open(full_doc_path, "w") as fd:
        identifier = ".".join(parts)
        fd.write(f"# `{identifier}`\n\n::: {identifier}\n")

    mkdocs_gen_files.set_edit_path(full_doc_path, path)


with mkdocs_gen_files.open("reference/SUMMARY.md", "w") as nav_file:
    nav_file.writelines(nav.build_literate_nav())
