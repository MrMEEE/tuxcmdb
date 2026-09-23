"""Parser for TuxCMDB asset filter expressions."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Predicate:
    name: str
    value: str


@dataclass(frozen=True)
class Not:
    operand: "Expression"


@dataclass(frozen=True)
class And:
    left: "Expression"
    right: "Expression"


@dataclass(frozen=True)
class Or:
    left: "Expression"
    right: "Expression"


Expression = Predicate | Not | And | Or


class FilterSyntaxError(ValueError):
    def __init__(self, message: str, position: int) -> None:
        super().__init__(f"{message} at position {position}")
        self.message = message
        self.position = position


@dataclass(frozen=True)
class _Token:
    kind: str
    value: str
    position: int


def _tokenize(source: str) -> list[_Token]:
    tokens: list[_Token] = []
    position = 0
    while position < len(source):
        char = source[position]
        if char.isspace():
            position += 1
            continue
        if char in "()=":
            kind = {"(": "LPAREN", ")": "RPAREN", "=": "EQUALS"}[char]
            tokens.append(_Token(kind, char, position))
            position += 1
            continue
        if char in "\"'":
            quote = char
            start = position
            position += 1
            value: list[str] = []
            while position < len(source) and source[position] != quote:
                if source[position] == "\\":
                    position += 1
                    if position >= len(source):
                        raise FilterSyntaxError("Unterminated escape sequence", position - 1)
                value.append(source[position])
                position += 1
            if position >= len(source):
                raise FilterSyntaxError("Unterminated quoted value", start)
            tokens.append(_Token("WORD", "".join(value), start))
            position += 1
            continue

        start = position
        while position < len(source) and not source[position].isspace() and source[position] not in "()=":
            position += 1
        value = source[start:position]
        keyword = value.upper()
        kind = keyword if keyword in {"AND", "OR", "NOT"} else "WORD"
        tokens.append(_Token(kind, value, start))

    tokens.append(_Token("EOF", "", len(source)))
    return tokens


class _Parser:
    def __init__(self, source: str) -> None:
        self.tokens = _tokenize(source)
        self.index = 0

    @property
    def current(self) -> _Token:
        return self.tokens[self.index]

    def consume(self, kind: str, message: str) -> _Token:
        token = self.current
        if token.kind != kind:
            raise FilterSyntaxError(message, token.position)
        self.index += 1
        return token

    def parse(self) -> Expression:
        if self.current.kind == "EOF":
            raise FilterSyntaxError("Filter expression is empty", 0)
        expression = self.parse_or()
        if self.current.kind != "EOF":
            raise FilterSyntaxError(f"Unexpected token '{self.current.value}'", self.current.position)
        return expression

    def parse_or(self) -> Expression:
        expression = self.parse_and()
        while self.current.kind == "OR":
            self.index += 1
            expression = Or(expression, self.parse_and())
        return expression

    def parse_and(self) -> Expression:
        expression = self.parse_not()
        while self.current.kind == "AND":
            self.index += 1
            expression = And(expression, self.parse_not())
        return expression

    def parse_not(self) -> Expression:
        if self.current.kind == "NOT":
            self.index += 1
            return Not(self.parse_not())
        return self.parse_primary()

    def parse_primary(self) -> Expression:
        if self.current.kind == "LPAREN":
            self.index += 1
            expression = self.parse_or()
            self.consume("RPAREN", "Expected ')'")
            return expression
        return self.parse_predicate()

    def parse_predicate(self) -> Expression:
        name = self.consume("WORD", "Expected an attribute name")
        if not name.value or not (name.value[0].isalpha() or name.value[0] == "_"):
            raise FilterSyntaxError("Invalid attribute name", name.position)
        if not all(char.isalnum() or char == "_" for char in name.value):
            raise FilterSyntaxError("Invalid attribute name", name.position)

        negate = self.current.kind == "NOT"
        if negate:
            self.index += 1
        else:
            self.consume("EQUALS", "Expected '=' or NOT after attribute name")

        value = self.consume("WORD", "Expected a filter value")
        if value.value == "":
            raise FilterSyntaxError("Filter value must not be empty", value.position)
        predicate: Expression = Predicate(name.value.lower(), value.value)
        return Not(predicate) if negate else predicate


def parse_filter(source: str) -> Expression:
    return _Parser(source).parse()