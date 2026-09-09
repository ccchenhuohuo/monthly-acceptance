"""Lossless historical-source coverage; no data access or market assumptions."""
import copy
import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from acceptance_history import segment_source, verify_history_coverage


def source(tmp_path, value="# Prior issues\n\n## A\nSKU A wrong class.\n## B\nSKU B wrong price.\n"):
    path = tmp_path / "history.md"
    path.write_text(value)
    return path, segment_source(path)


def mapping(segments):
    return [{**s, "obligation_ids": ["historical:known-A"],
             "reason": "此段说明历史商品A的问题与已登记调查义务的对应关系。"} for s in segments]


def test_every_nonblank_line_is_covered_once_across_headings_and_blank_lines(tmp_path):
    path, segments = source(tmp_path, "\n# Heading\ntext\n  \n## A\nitem A\n## B\nitem B\n\nlast paragraph")
    lines = path.read_bytes().decode().splitlines(keepends=True)
    actual = [line for s in segments for line in range(s["line_start"], s["line_end"] + 1)]
    assert sorted(actual) == [i for i, line in enumerate(lines, 1) if line.strip()]
    assert len(actual) == len(set(actual))
    assert [s["line_start"] for s in segments] == [2, 5, 7, 10]
    for s in segments:
        body = "".join(lines[s["line_start"] - 1:s["line_end"]]).encode()
        assert s["sha256"] == hashlib.sha256(body).hexdigest()


def test_ids_are_copy_stable_and_bound_to_the_whole_source_version(tmp_path):
    path, first = source(tmp_path)
    copied = tmp_path / "renamed.md"
    copied.write_bytes(path.read_bytes())
    assert segment_source(copied) == first
    path.write_text(path.read_text() + "\nNew issue C\n")
    assert not {s["segment_id"] for s in first} & {s["segment_id"] for s in segment_source(path)}


def test_crlf_indentation_and_unicode_are_hashed_without_rewriting(tmp_path):
    path = tmp_path / "crlf.md"
    path.write_bytes("# 旧记录\r\n\r\n  商品甲\r\n    规格乙".encode())
    rows = segment_source(path)
    assert rows[1]["sha256"] == hashlib.sha256("  商品甲\r\n    规格乙".encode()).hexdigest()


def test_skipping_issue_b_is_rejected_even_when_a_has_valid_obligation(tmp_path):
    _, segments = source(tmp_path)
    entries = mapping(segments[:-1])
    with pytest.raises(ValueError, match="未覆盖分段"):
        verify_history_coverage(segments, entries, {"historical:known-A"})


def test_complete_mapping_and_specific_non_issue_entry_pass(tmp_path):
    _, segments = source(tmp_path)
    entries = mapping(segments)
    entries[0] = {**segments[0], "not_applicable_reason": "此段仅为文档总标题，不包含独立数据问题。"}
    result = verify_history_coverage(segments, entries, {"historical:known-A"})
    assert result == {"segments": 3, "mapped_segments": 2, "not_applicable_segments": 1,
                      "obligation_ids": ["historical:known-A"]}


@pytest.mark.parametrize("change", ["duplicate", "unknown", "wrong_lines", "wrong_hash", "bool_line"])
def test_segment_identity_and_locator_cannot_be_faked(tmp_path, change):
    _, segments = source(tmp_path)
    entries = mapping(segments)
    if change == "duplicate":
        entries[-1] = copy.deepcopy(entries[0])
    elif change == "unknown":
        entries[-1]["segment_id"] = "segment-" + "f" * 24
    elif change == "wrong_lines":
        entries[-1]["line_start"] += 1
    elif change == "wrong_hash":
        entries[-1]["sha256"] = "0" * 64
    else:
        entries[0]["line_start"] = True
    with pytest.raises(ValueError):
        verify_history_coverage(segments, entries, {"historical:known-A"})


def test_arbitrary_old_locator_without_segment_fields_is_rejected(tmp_path):
    _, segments = source(tmp_path)
    with pytest.raises(ValueError):
        verify_history_coverage(segments, [{"locator": "Issue A", "obligation_ids": ["historical:known-A"]}],
                                {"historical:known-A"})


@pytest.mark.parametrize("reason", ["", "不适用", "没有发现问题", "same as above", "   "])
def test_generic_non_issue_explanations_cannot_skip_a_segment(tmp_path, reason):
    _, segments = source(tmp_path)
    entries = mapping(segments)
    entries[-1] = {**segments[-1], "not_applicable_reason": reason}
    with pytest.raises(ValueError, match="说明"):
        verify_history_coverage(segments, entries, {"historical:known-A"})


@pytest.mark.parametrize("mutation", ["unknown_id", "duplicate_id", "missing_reason", "ambiguous"])
def test_issue_mapping_requires_registered_unique_ids_and_specific_reason(tmp_path, mutation):
    _, segments = source(tmp_path)
    entries = mapping(segments)
    if mutation == "unknown_id":
        entries[-1]["obligation_ids"] = ["historical:missing-B"]
    elif mutation == "duplicate_id":
        entries[-1]["obligation_ids"] *= 2
    elif mutation == "missing_reason":
        entries[-1].pop("reason")
    else:
        entries[-1]["not_applicable_reason"] = "此段只是没有独立事项的说明。"
    with pytest.raises(ValueError):
        verify_history_coverage(segments, entries, {"historical:known-A"})


def test_blank_source_has_no_invented_obligations(tmp_path):
    _, segments = source(tmp_path, " \n\t\n")
    assert segments == []
    assert verify_history_coverage(segments, [], set())["segments"] == 0


def test_corrupt_source_segment_list_is_rejected(tmp_path):
    _, segments = source(tmp_path)
    broken = segments + [segments[0]]
    with pytest.raises(ValueError, match="分段ID"):
        verify_history_coverage(broken, mapping(broken), {"historical:known-A"})
