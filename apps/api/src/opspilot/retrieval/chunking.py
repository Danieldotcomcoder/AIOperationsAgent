"""Heading-preserving, deterministic UTF-8 token windows.

The byte tokenizer is deliberately conservative (one UTF-8 byte per token), fully
offline, and never underestimates the text's model token budget. Overlap is in bytes;
windows split only at Unicode character boundaries. No downloaded tokenizer assets.
"""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class TextChunk:
    heading_path: str
    text: str
    token_count: int


def chunk_text(text: str, target: int = 500, overlap: int = 75) -> list[TextChunk]:
    if target < 4 or not 0 <= overlap < target:
        raise ValueError("Invalid chunk bounds")
    sections: list[tuple[str, str]] = []
    headings: list[tuple[int, str]] = []
    body: list[str] = []
    path = ""
    fenced = False
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        match = None if fenced else re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if match:
            if body:
                sections.append((path, "".join(body)))
            level, title = len(match[1]), match[2]
            headings = [(n, name) for n, name in headings if n < level]
            headings.append((level, title))
            path = " > ".join(name for _, name in headings)[:500]
            body = [line]
        else:
            body.append(line)
    if body:
        sections.append((path, "".join(body)))
    result = []
    for heading, content in sections:
        start = 0
        while start < len(content):
            end, size = start, 0
            while end < len(content) and size + len(content[end].encode()) <= target:
                size += len(content[end].encode())
                end += 1
            passage = content[start:end]
            if passage.strip():
                result.append(TextChunk(heading, passage, size))
            if end == len(content):
                break
            next_start, overlap_size = end, 0
            while next_start > start + 1:
                width = len(content[next_start - 1].encode())
                if overlap_size + width > overlap:
                    break
                overlap_size += width
                next_start -= 1
            start = next_start
    return result
