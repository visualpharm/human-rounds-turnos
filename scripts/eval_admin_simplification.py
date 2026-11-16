#!/usr/bin/env python3
"""Evaluate four bounded admin-simplification DOM contracts.

This is deliberately a DOM evaluator, not a source-text ban.  A rendered
fixture declares the semantic role of a control/row with data-admin-* markers,
then the evaluator checks the visible descendants.  Product components must
still receive a semantic review when their rendered fixture is unavailable.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
import sys


@dataclass
class Node:
    tag: str
    attrs: dict[str, str]
    parent: "Node | None" = None
    children: list["Node"] = None  # type: ignore[assignment]
    text: str = ""

    def __post_init__(self):
        self.children = [] if self.children is None else self.children

    def descendants(self):
        for child in self.children:
            yield child
            yield from child.descendants()

    def is_hidden(self) -> bool:
        style = self.attrs.get("style", "").replace(" ", "").lower()
        return ("hidden" in self.attrs or self.attrs.get("aria-hidden") == "true" or
                "display:none" in style or "visibility:hidden" in style or
                (self.parent is not None and self.parent.is_hidden()))

    def visible_text(self) -> str:
        if self.is_hidden():
            return ""
        return " ".join((self.text + " " + " ".join(c.visible_text() for c in self.children)).split())


class Tree(HTMLParser):
    def __init__(self):
        super().__init__()
        self.root = Node("root", {})
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, dict(attrs), self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in {"input", "img", "br", "hr", "meta", "link"}:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in {"input", "img", "br", "hr", "meta", "link"}:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1].text += data


def nodes(root, predicate):
    return [node for node in root.descendants() if predicate(node)]


def visible(node: Node) -> bool:
    return not node.is_hidden()


def evaluate(root: Node) -> list[str]:
    errors: list[str] = []
    marker_names = {
        "data-admin-binary", "data-admin-optional", "data-admin-repeated-mutation",
        "data-admin-empty", "data-admin-selection", "data-admin-group-action",
        "data-admin-create",
    }
    if not any(marker in node.attrs for node in root.descendants() for marker in marker_names):
        errors.append("coverage unavailable: rendered admin snapshot has no data-admin semantic markers")
    # A binary, editable setting must be an actual checkbox.  Three or more
    # semantic select options (inheritance/status/etc.) are intentionally out
    # of scope and remain valid.
    for setting in nodes(root, lambda n: n.attrs.get("data-admin-binary") == "editable"):
        controls = nodes(setting, lambda n: n.tag in {"input", "select"} and visible(n))
        if not any(c.tag == "input" and c.attrs.get("type") == "checkbox" for c in controls):
            errors.append("editable binary setting has no checkbox")
        for control in controls:
            if control.tag == "select":
                options = nodes(control, lambda n: n.tag == "option")
                if len(options) == 2:
                    errors.append("editable binary setting uses a two-option select")

    # This catches a common unannotated rendered regression without guessing
    # that every two-option select is boolean.  Filters must declare their
    # context; semantic selects with any other values are left alone.
    for select in nodes(root, lambda n: n.tag == "select" and visible(n) and
                        n.attrs.get("data-admin-context") != "filter"):
        options = nodes(select, lambda n: n.tag == "option" and visible(n))
        values = [(option.attrs.get("value") or option.visible_text()).strip().lower() for option in options]
        if len(values) == 2 and set(values) in ({"true", "false"}, {"yes", "no"}):
            errors.append("boolean Yes/No select detected; use a checkbox or mark this as a filter")

    # An unavailable optional property has no visible placeholder, label or
    # disabled action.  The property wrapper itself is opt-in evidence, so an
    # absent property is naturally a pass.
    for prop in nodes(root, lambda n: n.attrs.get("data-admin-optional") == "unavailable"):
        property_text = prop.visible_text()
        if property_text or nodes(prop, lambda n: n.tag in {"a", "button", "input", "select", "label"} and not n.is_hidden()):
            errors.append("unavailable optional property is still rendered")

    # Repeated same-row mutation must expose either visible selection/group
    # operation or Shift range selection.  A row with no mutation marker is
    # not a bulk-work assertion.
    for group in nodes(root, lambda n: n.attrs.get("data-admin-repeated-mutation") == "true"):
        selection = nodes(group, lambda n: n.attrs.get("data-admin-selection") == "visible" and visible(n))
        group_action = nodes(group, lambda n: n.attrs.get("data-admin-group-action") == "true" and visible(n))
        range_ok = group.attrs.get("data-admin-shift-range") == "true"
        if not range_ok and (not selection or not group_action):
            errors.append("repeated row mutation needs visible selection plus group action, or Shift range")

    # Empty editable sections need a creation action/form.  Empty selected
    # filters, failures, and read-only sections are explicitly declared
    # exemptions and never become accidental creation prompts.
    for section in nodes(root, lambda n: n.attrs.get("data-admin-empty") == "editable"):
        create = nodes(section, lambda n: n.attrs.get("data-admin-create") == "true" and n.tag in {"a", "button", "form"} and visible(n))
        if not create:
            errors.append("editable empty section has no creation action/form")
    return errors


def check(path: Path) -> list[str]:
    parser = Tree()
    parser.feed(path.read_text(encoding="utf-8"))
    return [f"{path}: {error}" for error in evaluate(parser.root)]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--fixtures", action="store_true", help="evaluate the committed positive and negative fixtures")
    args = parser.parse_args(argv)
    paths = args.paths
    if args.fixtures:
        # Negative fixtures are asserted individually by the unit test.  The
        # hook runs only clean fixtures, so a known regression fixture never
        # makes every unrelated admin edit fail.
        paths += sorted(Path("tests/fixtures/admin-simplification").glob("clean*.html"))
    if not paths:
        parser.error("provide rendered HTML paths or --fixtures")
    errors = [error for path in paths for error in check(path)]
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(f"admin simplification evaluation passed ({len(paths)} fixture(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
