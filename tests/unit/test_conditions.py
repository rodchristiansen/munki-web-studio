import pytest

from munki_web_studio.conditions import analyze


@pytest.mark.parametrize(
    ("text", "rule"),
    [
        (
            'machine_type == "laptop" AND os_vers_major >= 14',
            'machine type is "laptop" and macOS major version is at least 14',
        ),
        ("hostname BEGINSWITH[c] 'LAB-'", "hostname begins with (ignoring case) 'LAB-'"),
        ('ANY catalogs == "testing"', 'any catalogs is "testing"'),
        (
            "NOT (arch == 'arm64' OR arch == 'x86_64')",
            "not (architecture is 'arm64' or architecture is 'x86_64')",
        ),
        (
            'machine_model IN {"MacBookPro18,1", "Mac14,2"}',
            'Mac model is one of ["MacBookPro18,1", "Mac14,2"]',
        ),
        (
            'date > CAST("2026-01-01T00:00:00Z", "NSDate")',
            'date is more than "2026-01-01T00:00:00Z"',
        ),
        ("a == 1 && (b == 2 || c != 3)", "a is 1 and (b is 2 or c is not 3)"),
        ("os_vers MATCHES '14\\\\..*'", "macOS version matches the regular expression '14\\\\..*'"),
    ],
)
def test_conditions_read_as_rules(text: str, rule: str) -> None:
    report = analyze(text)
    assert report.error is None
    assert report.rule == rule


def test_unknown_facts_are_reported_but_allowed() -> None:
    report = analyze('site_department == "IT" AND arch == "arm64"')
    assert report.rule is not None
    assert report.facts == ["site_department", "arch"]
    assert report.unknown_facts == ["site_department"]


@pytest.mark.parametrize(
    ("text", "column"),
    [
        ('machine_type == "laptop" AND', 29),
        ("hostname BEGINS 'LAB'", 10),
        ("(arch == 'arm64'", 17),
        ("arch == 'arm64' #", 17),
        ("os_vers_major >=", 17),
    ],
)
def test_syntax_errors_carry_a_column(text: str, column: int) -> None:
    report = analyze(text)
    assert report.rule is None and report.error
    assert report.column == column
