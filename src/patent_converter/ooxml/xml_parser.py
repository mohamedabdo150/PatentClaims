"""Secure XML parsing for untrusted OOXML parts.

OOXML never legitimately contains DTDs or entity declarations, so any part that
declares them is rejected outright. lxml parsers are additionally configured to
never resolve entities, load DTDs or access the network.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import IO

from lxml import etree

_PEEK_BYTES = 64 * 1024


class UnsafeXmlError(Exception):
    """Raised when an XML part contains constructs that are never allowed (DTD/entities)."""


def make_parser(huge_tree: bool = False) -> etree.XMLParser:
    return etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        dtd_validation=False,
        huge_tree=huge_tree,
        remove_comments=False,
        remove_pis=True,
    )


def check_prolog(head: bytes) -> None:
    lowered = head.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise UnsafeXmlError("XML part declares a DTD or entity (not permitted in OOXML)")


def parse_bytes(data: bytes, huge_tree: bool = False) -> etree._Element:
    check_prolog(data[:_PEEK_BYTES])
    return etree.fromstring(data, parser=make_parser(huge_tree))


class _PeekedStream:
    """File-like wrapper that checks the prolog of a stream before lxml consumes it."""

    def __init__(self, stream: IO[bytes]):
        self._stream = stream
        self._buffer = stream.read(_PEEK_BYTES)
        check_prolog(self._buffer)

    def read(self, size: int = -1) -> bytes:
        if self._buffer:
            if size is None or size < 0:
                data, self._buffer = self._buffer + self._stream.read(), b""
                return data
            data, self._buffer = self._buffer[:size], self._buffer[size:]
            if len(data) < size:
                data += self._stream.read(size - len(data))
            return data
        return self._stream.read(size)


def iterparse_secure(
    stream: IO[bytes], events: tuple[str, ...] = ("start", "end"), huge_tree: bool = False
) -> Iterator[tuple[str, etree._Element]]:
    """Incremental, secure iterparse over an XML stream."""
    yield from etree.iterparse(
        _PeekedStream(stream),
        events=events,
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        dtd_validation=False,
        huge_tree=huge_tree,
        remove_pis=True,
    )
