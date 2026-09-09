"""Deterministic, lossless inventories for frozen UTF-8 historical sources.

Each nonblank source line belongs to exactly one segment. Coverage entries must
identify every segment by its generated ID, exact line range, and content hash.
The coordinator still judges whether each mapping or non-issue explanation is
substantively correct; this module prevents silently skipping source paragraphs.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re


HEADING = re.compile(r"^ {0,3}#{1,6}(?:\s|$)")
SEGMENT_ID = re.compile(r"segment-[0-9a-f]{24}")
SHA256 = re.compile(r"[0-9a-f]{64}")
GENERIC_REASONS = {
    "不适用", "无问题", "正常", "已完成", "已覆盖", "同上", "略", "见报告",
    "无需处理", "没有问题", "没有发现问题", "不需要核查", "无需进一步处理",
    "notapplicable", "na", "none", "normal", "done", "covered", "sameasabove",
}


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def segment_source(path: str | Path) -> list[dict]:
    """Return stable segments; copying the same file does not change their IDs.

    Blank lines delimit paragraphs; ATX headings start a new segment even when
    the author omitted a blank line. Original indentation and line endings are
    retained in each SHA256. IDs bind the whole source hash, line range, and
    segment hash, so coverage from a different source version cannot be reused.
    """
    raw = Path(path).read_bytes()
    source_hash = _sha(raw)
    lines = raw.decode("utf-8").splitlines(keepends=True)
    result: list[dict] = []
    start: int | None = None
    part: list[str] = []

    def flush(end: int) -> None:
        nonlocal start, part
        if start is None:
            return
        sha = _sha("".join(part).encode("utf-8"))
        identity = json.dumps([source_hash, start, end, sha], separators=(",", ":")).encode()
        result.append({"segment_id": "segment-" + _sha(identity)[:24],
                       "line_start": start, "line_end": end, "sha256": sha})
        start, part = None, []

    for number, line in enumerate(lines, 1):
        if not line.strip():
            flush(number - 1)
            continue
        if HEADING.match(line) and part:
            flush(number - 1)
        if start is None:
            start = number
        part.append(line)
    flush(len(lines))
    return result


def _specific(value, label: str) -> None:
    if not isinstance(value, str):
        raise ValueError("历史逐段说明缺失：" + label)
    compact = re.sub(r"[\W_]+", "", value, flags=re.UNICODE).casefold()
    if len(compact) < 8 or compact in GENERIC_REASONS:
        raise ValueError("历史逐段说明过于笼统：" + label)


def verify_history_coverage(segments: list[dict], entries: list[dict], known_ids) -> dict:
    """Require one substantive disposition for every exact frozen segment.

    An entry repeats ``segment_id``, ``line_start``, ``line_end``, and ``sha256``.
    A mapped entry provides a nonempty unique ``obligation_ids`` list and a
    specific ``reason`` describing the mapping. A non-issue entry supplies
    ``not_applicable_reason`` and no obligation IDs. Human-readable ``locator``
    strings are not accepted as substitutes for the required structured range.
    The wording check is only a guard against empty/template answers; it cannot
    determine whether the coordinator has correctly understood the source.
    """
    if not isinstance(segments, list) or not isinstance(entries, list):
        raise ValueError("历史来源分段和覆盖必须为显式列表")
    if not isinstance(known_ids, (set, frozenset, list, tuple)) or any(
            not isinstance(oid, str) or not oid.strip() for oid in known_ids):
        raise ValueError("已登记调查义务ID集合无效")
    known = set(known_ids)
    expected = {}
    occupied = set()
    for segment in segments:
        if not isinstance(segment, dict):
            raise ValueError("历史来源分段无效")
        sid = segment.get("segment_id")
        if not isinstance(sid, str) or not SEGMENT_ID.fullmatch(sid) or sid in expected:
            raise ValueError("历史来源分段ID无效或重复")
        a, b, sha = segment.get("line_start"), segment.get("line_end"), segment.get("sha256")
        if type(a) is not int or type(b) is not int or not 1 <= a <= b:
            raise ValueError("历史来源分段行范围无效")
        if not isinstance(sha, str) or not SHA256.fullmatch(sha):
            raise ValueError("历史来源分段哈希无效")
        covered = set(range(a, b + 1))
        if covered & occupied:
            raise ValueError("历史来源分段行范围重叠")
        occupied.update(covered)
        expected[sid] = segment
    seen, mapped_ids = set(), set()
    mapped, not_applicable = 0, 0
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("历史逐段覆盖项无效")
        sid = entry.get("segment_id")
        if not isinstance(sid, str) or sid not in expected or sid in seen:
            raise ValueError("历史覆盖包含未知或重复分段")
        seen.add(sid)
        segment = expected[sid]
        for key in ("line_start", "line_end", "sha256"):
            if entry.get(key) != segment[key] or type(entry.get(key)) is not type(segment[key]):
                raise ValueError("历史覆盖定位或哈希与冻结分段不一致：" + sid)
        ids = entry.get("obligation_ids", [])
        if not isinstance(ids, list) or any(not isinstance(oid, str) or not oid.strip() for oid in ids):
            raise ValueError("历史覆盖义务ID必须为显式有效列表")
        if len(ids) != len(set(ids)) or not set(ids) <= known:
            raise ValueError("历史覆盖含重复或未登记调查义务")
        if ids:
            if entry.get("not_applicable_reason"):
                raise ValueError("历史分段不能同时登记义务和声明不适用")
            _specific(entry.get("reason"), "问题与义务对应关系")
            mapped_ids.update(ids)
            mapped += 1
        else:
            _specific(entry.get("not_applicable_reason"), "非事项的具体理由")
            not_applicable += 1
    if seen != set(expected):
        missing = sorted(set(expected) - seen)
        raise ValueError("历史来源存在未覆盖分段：" + ",".join(missing))
    return {"segments": len(expected), "mapped_segments": mapped,
            "not_applicable_segments": not_applicable, "obligation_ids": sorted(mapped_ids)}
