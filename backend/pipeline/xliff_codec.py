"""XLIFF text view codec for native DOCX translation."""

from __future__ import annotations

import re
from copy import deepcopy
from xml.etree import ElementTree as ET


XLIFF_NS = "urn:oasis:names:tc:xliff:document:1.2"
INLINE_LOCAL_NAMES = {"bpt", "ept", "ph", "g", "x", "bx", "ex", "it", "sub"}
PAIRED_INLINE_NAMES = {"g", "sub"}
PLACEHOLDER_RE = re.compile(r"\[\[TA_[A-Z0-9]+(?:_START|_END)?\]\]")


class XliffCodecError(ValueError):
    """XLIFF contract failure with a stable document error code prefix."""


def xliff_error(code: str, detail: str) -> XliffCodecError:
    return XliffCodecError(f"{code}: {detail}")


def local_name(tag: str) -> str:
    if tag.startswith("{"):
        return tag.rsplit("}", 1)[1]
    return tag


def namespace_of(tag: str) -> str:
    if tag.startswith("{"):
        return tag[1:].split("}", 1)[0]
    return ""


def qname(namespace: str, name: str) -> str:
    return f"{{{namespace}}}{name}" if namespace else name


def visible_text(element: ET.Element) -> str:
    parts: list[str] = []
    if element.text:
        parts.append(element.text)
    for child in list(element):
        parts.append(visible_text(child))
        if child.tail:
            parts.append(child.tail)
    return "".join(parts)


def encode_source(source: ET.Element) -> tuple[str, dict]:
    """Encode a XLIFF source element to user-facing text and restore metadata."""
    counters: dict[str, int] = {}
    events: list[dict] = []
    parts: list[str] = []

    def next_index(name: str) -> int:
        counters[name] = counters.get(name, 0) + 1
        return counters[name]

    def append_node_content(node: ET.Element) -> None:
        if node.text:
            parts.append(node.text)
        for child in list(node):
            append_child(child)

    def append_child(child: ET.Element) -> None:
        name = local_name(child.tag)
        if name not in INLINE_LOCAL_NAMES:
            raise xliff_error("DOCUMENT_EXTRACTION_ERROR", f"unsupported inline XML element: {name}")
        index = next_index(name.upper())
        if name in PAIRED_INLINE_NAMES:
            start = f"[[TA_{name.upper()}{index}_START]]"
            end = f"[[TA_{name.upper()}{index}_END]]"
            events.append({
                "kind": "start",
                "token": start,
                "end_token": end,
                "tag": child.tag,
                "attrs": dict(child.attrib),
                "name": name,
            })
            parts.append(start)
            append_node_content(child)
            events.append({
                "kind": "end",
                "token": end,
                "start_token": start,
                "tag": child.tag,
                "name": name,
            })
            parts.append(end)
        else:
            token = f"[[TA_{name.upper()}{index}]]"
            clone = deepcopy(child)
            clone.tail = None
            events.append({
                "kind": "atom",
                "token": token,
                "tag": child.tag,
                "attrs": dict(child.attrib),
                "xml": ET.tostring(clone, encoding="unicode"),
                "name": name,
            })
            parts.append(token)
        if child.tail:
            parts.append(child.tail)

    append_node_content(source)
    tokens = [event["token"] for event in events]
    return "".join(parts), {
        "placeholder_events": events,
        "placeholder_tokens": tokens,
        "placeholder_signature": placeholder_signature_from_tokens(tokens),
    }


def placeholder_tokens(text: str) -> list[str]:
    return [match.group(0) for match in PLACEHOLDER_RE.finditer(text)]


def placeholder_signature_from_tokens(tokens: list[str]) -> list[dict]:
    counts: dict[str, int] = {}
    signature: list[dict] = []
    for token in tokens:
        counts[token] = counts.get(token, 0) + 1
        signature.append({"token": token, "ordinal": counts[token]})
    return signature


def assert_placeholder_contract(text: str, metadata: dict) -> None:
    expected = list(metadata.get("placeholder_tokens", []))
    actual = placeholder_tokens(text)
    if actual != expected:
        raise xliff_error(
            "DOCUMENT_PLACEHOLDER_ERROR",
            "placeholder sequence changed; translations must preserve every placeholder exactly and in order",
        )
    if "[[TA_" in PLACEHOLDER_RE.sub("", text):
        raise xliff_error("DOCUMENT_PLACEHOLDER_ERROR", "malformed internal placeholder remains in translated text")


def restore_target(source: ET.Element, translated_text: str, metadata: dict, target_lang: str) -> ET.Element:
    """Create a XLIFF target element by restoring inline XML around text."""
    assert_placeholder_contract(translated_text, metadata)
    namespace = namespace_of(source.tag)
    target = ET.Element(qname(namespace, "target"))
    target.set("{http://www.w3.org/XML/1998/namespace}lang", target_lang)

    events_by_token = {event["token"]: event for event in metadata.get("placeholder_events", [])}
    pieces = _split_by_placeholders(translated_text)
    stack: list[ET.Element] = [target]
    start_stack: list[dict] = []

    for is_token, value in pieces:
        if not is_token:
            _append_text(stack[-1], value)
            continue
        event = events_by_token.get(value)
        if event is None:
            raise xliff_error("DOCUMENT_PLACEHOLDER_ERROR", "unknown placeholder in translated text")
        if event["kind"] == "atom":
            node = ET.fromstring(event["xml"])
            node.tail = None
            stack[-1].append(node)
        elif event["kind"] == "start":
            node = ET.Element(event["tag"], event.get("attrs", {}))
            stack[-1].append(node)
            stack.append(node)
            start_stack.append(event)
        elif event["kind"] == "end":
            if not start_stack or start_stack[-1].get("end_token") != value:
                raise xliff_error("DOCUMENT_PLACEHOLDER_ERROR", "placeholder nesting changed")
            start_stack.pop()
            stack.pop()

    if len(stack) != 1 or start_stack:
        raise xliff_error("DOCUMENT_PLACEHOLDER_ERROR", "unclosed inline placeholder")
    return target


def inline_signature(element: ET.Element) -> list[dict]:
    """Return a stable inline XML structure signature excluding translatable text."""
    signature: list[dict] = []

    def walk(node: ET.Element, path: str) -> None:
        for index, child in enumerate(list(node)):
            name = local_name(child.tag)
            child_path = f"{path}/{name}[{index}]"
            if name in INLINE_LOCAL_NAMES:
                item = {
                    "path": child_path,
                    "tag": child.tag,
                    "attrs": sorted(child.attrib.items()),
                    "kind": "paired" if name in PAIRED_INLINE_NAMES else "atom",
                }
                if name not in PAIRED_INLINE_NAMES:
                    clone = deepcopy(child)
                    clone.tail = None
                    item["xml"] = ET.tostring(clone, encoding="unicode")
                signature.append(item)
                if name in PAIRED_INLINE_NAMES:
                    walk(child, child_path)
            elif namespace_of(child.tag) == XLIFF_NS:
                raise xliff_error("DOCUMENT_PLACEHOLDER_ERROR", f"unknown inline XML element: {name}")
            else:
                walk(child, child_path)

    walk(element, "")
    return signature


def _split_by_placeholders(text: str) -> list[tuple[bool, str]]:
    pieces: list[tuple[bool, str]] = []
    pos = 0
    for match in PLACEHOLDER_RE.finditer(text):
        if match.start() > pos:
            pieces.append((False, text[pos:match.start()]))
        pieces.append((True, match.group(0)))
        pos = match.end()
    if pos < len(text):
        pieces.append((False, text[pos:]))
    return pieces


def _append_text(parent: ET.Element, text: str) -> None:
    if not text:
        return
    children = list(parent)
    if children:
        last = children[-1]
        last.tail = (last.tail or "") + text
    else:
        parent.text = (parent.text or "") + text
