"""Munki conditions: parse the NSPredicate subset Munki manifests use, for
display and validation. Nothing here evaluates a condition against a machine.

    analyze('machine_type == "laptop" AND os_vers_major >= 14')
    -> rule: 'machine type is "laptop" and macOS major version is at least 14'

Unknown facts are allowed (admin-provided condition scripts add their own) and
reported as warnings; syntax errors carry a 1-based column.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Facts Munki provides itself. Admin condition scripts can add more.
KNOWN_FACTS: dict[str, str] = {
    "hostname": "hostname",
    "arch": "architecture",
    "os_vers": "macOS version",
    "os_vers_major": "macOS major version",
    "os_vers_minor": "macOS minor version",
    "os_vers_patch": "macOS patch version",
    "os_build_number": "macOS build",
    "os_build_last_component": "macOS build number",
    "machine_model": "Mac model",
    "machine_type": "machine type",
    "physical_or_virtual": "physical or virtual",
    "ipv4_address": "IPv4 addresses",
    "munki_version": "Munki version",
    "serial_number": "serial number",
    "product_name": "product name",
    "board_id": "board ID",
    "device_id": "device ID",
    "date": "date",
    "catalogs": "catalogs",
    "applications": "installed applications",
}

_COMPARISONS = {
    "==": "is",
    "=": "is",
    "!=": "is not",
    "<>": "is not",
    "<": "is less than",
    "<=": "is at most",
    ">": "is more than",
    ">=": "is at least",
    "LIKE": "matches the pattern",
    "BEGINSWITH": "begins with",
    "ENDSWITH": "ends with",
    "CONTAINS": "contains",
    "MATCHES": "matches the regular expression",
    "IN": "is one of",
}

_TOKEN = re.compile(
    r"""
    (?P<space>\s+)
  | (?P<string>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')
  | (?P<number>-?\d+(?:\.\d+)?)
  | (?P<op>==|!=|<>|<=|>=|&&|\|\||[=<>!(){},\[\]])
  | (?P<word>[A-Za-z_$@][A-Za-z0-9_.$@]*)
    """,
    re.VERBOSE,
)


class ConditionError(ValueError):
    def __init__(self, message: str, column: int) -> None:
        super().__init__(f"{message} (column {column})")
        self.message = message
        self.column = column


@dataclass(frozen=True)
class Token:
    kind: str
    text: str
    column: int


@dataclass
class Node:
    """One parsed expression."""

    kind: str  # "and", "or", "not", "compare", "value", "key", "list", "cast"
    text: str = ""
    children: list[Node] = field(default_factory=lambda: [])
    modifier: str = ""
    quantifier: str = ""


@dataclass(frozen=True)
class ConditionReport:
    text: str
    rule: str | None
    error: str | None
    column: int | None
    facts: list[str]
    unknown_facts: list[str]


def _tokens(text: str) -> list[Token]:
    tokens: list[Token] = []
    position = 0
    while position < len(text):
        match = _TOKEN.match(text, position)
        if not match:
            raise ConditionError(f"Unexpected character {text[position]!r}", position + 1)
        kind = match.lastgroup or ""
        if kind != "space":
            tokens.append(Token(kind, match.group(), position + 1))
        position = match.end()
    tokens.append(Token("end", "", len(text) + 1))
    return tokens


class _Parser:
    def __init__(self, text: str) -> None:
        self.tokens = _tokens(text)
        self.position = 0

    def peek(self) -> Token:
        return self.tokens[self.position]

    def take(self) -> Token:
        token = self.tokens[self.position]
        self.position += 1
        return token

    def word(self, *words: str) -> bool:
        token = self.peek()
        return token.kind == "word" and token.text.upper() in words

    def expect(self, text: str) -> Token:
        token = self.take()
        if token.text != text:
            found = token.text or "the end"
            raise ConditionError(f"Expected {text!r} but found {found!r}", token.column)
        return token

    def parse(self) -> Node:
        node = self.parse_or()
        if self.peek().kind != "end":
            raise ConditionError(f"Unexpected {self.peek().text!r}", self.peek().column)
        return node

    def parse_or(self) -> Node:
        node = self.parse_and()
        while self.word("OR") or self.peek().text == "||":
            self.take()
            node = Node("or", children=[node, self.parse_and()])
        return node

    def parse_and(self) -> Node:
        node = self.parse_not()
        while self.word("AND") or self.peek().text == "&&":
            self.take()
            node = Node("and", children=[node, self.parse_not()])
        return node

    def parse_not(self) -> Node:
        if self.word("NOT") or self.peek().text == "!":
            self.take()
            return Node("not", children=[self.parse_not()])
        if self.peek().text == "(":
            self.take()
            node = self.parse_or()
            self.expect(")")
            return node
        return self.parse_comparison()

    def parse_comparison(self) -> Node:
        quantifier = ""
        if self.word("ANY", "ALL", "NONE", "SOME"):
            quantifier = self.take().text.upper()
        left = self.parse_operand()
        token = self.peek()
        operator = token.text.upper() if token.kind == "word" else token.text
        if operator not in _COMPARISONS:
            found = token.text or "the end"
            raise ConditionError(f"Expected a comparison but found {found!r}", token.column)
        self.take()
        modifier = ""
        if self.peek().text == "[":
            self.take()
            modifier = self.take().text.lower()
            self.expect("]")
        right = self.parse_operand()
        return Node("compare", operator, [left, right], modifier, quantifier)

    def parse_operand(self) -> Node:
        token = self.take()
        if token.kind == "string":
            return Node("value", token.text)
        if token.kind == "number":
            return Node("value", token.text)
        if token.text == "{":
            items: list[Node] = []
            while self.peek().text != "}":
                items.append(self.parse_operand())
                if self.peek().text == ",":
                    self.take()
                elif self.peek().text != "}":
                    raise ConditionError("Expected ',' or '}'", self.peek().column)
            self.take()
            return Node("list", children=items)
        if token.kind == "word":
            upper = token.text.upper()
            if upper in ("TRUE", "YES", "FALSE", "NO", "NIL", "NULL"):
                return Node("value", upper)
            if upper == "CAST" and self.peek().text == "(":
                self.take()
                value = self.parse_operand()
                self.expect(",")
                kind = self.parse_operand()
                self.expect(")")
                return Node("cast", kind.text, [value])
            return Node("key", token.text)
        found = token.text or "the end"
        raise ConditionError(f"Expected a fact or a value but found {found!r}", token.column)


def parse(text: str) -> Node:
    return _Parser(text).parse()


def _facts(node: Node) -> list[str]:
    found: list[str] = []
    if node.kind == "key":
        found.append(node.text.split(".")[0])
    for child in node.children:
        found += _facts(child)
    return found


def _describe(node: Node) -> str:
    match node.kind:
        case "and":
            return " and ".join(_group(c, "or") for c in node.children)
        case "or":
            return " or ".join(_group(c, "and") for c in node.children)
        case "not":
            return f"not ({_describe(node.children[0])})"
        case "key":
            return KNOWN_FACTS.get(node.text, node.text)
        case "list":
            return "[" + ", ".join(_describe(c) for c in node.children) + "]"
        case "cast":
            return _describe(node.children[0])
        case "compare":
            left, right = node.children
            phrase = _COMPARISONS[node.text]
            if "c" in node.modifier:
                phrase += " (ignoring case)"
            quantifier = {
                "ANY": "any ",
                "SOME": "any ",
                "ALL": "every ",
                "NONE": "no ",
            }.get(node.quantifier, "")
            return f"{quantifier}{_describe(left)} {phrase} {_describe(right)}"
        case _:
            return node.text


def _group(node: Node, other: str) -> str:
    text = _describe(node)
    return f"({text})" if node.kind == other else text


def analyze(text: str) -> ConditionReport:
    try:
        tree = parse(text)
    except ConditionError as error:
        return ConditionReport(text, None, error.message, error.column, [], [])
    facts = list(dict.fromkeys(_facts(tree)))
    unknown = [f for f in facts if f not in KNOWN_FACTS]
    return ConditionReport(text, _describe(tree), None, None, facts, unknown)
