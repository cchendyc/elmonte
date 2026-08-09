from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ParsedName:
    firstname: str
    middlename: str | None
    lastname: str


def parse_full_name(full_name: str) -> ParsedName:
    parts = full_name.strip().split()
    if not parts:
        raise ValueError("name must not be empty")
    if len(parts) == 1:
        return ParsedName(firstname=parts[0], middlename=None, lastname=parts[0])
    if len(parts) == 2:
        return ParsedName(firstname=parts[0], middlename=None, lastname=parts[1])
    return ParsedName(
        firstname=parts[0],
        middlename=" ".join(parts[1:-1]),
        lastname=parts[-1],
    )


def format_full_name(
    firstname: str, middlename: str | None, lastname: str
) -> str:
    parts = [firstname]
    if middlename:
        parts.append(middlename)
    parts.append(lastname)
    return " ".join(parts)


def normalize_full_name(full_name: str) -> str:
    parsed = parse_full_name(full_name)
    return normalize_person_name(parsed.firstname, parsed.middlename, parsed.lastname)


def normalize_person_name(
    firstname: str, middlename: str | None, lastname: str
) -> str:
    return format_full_name(firstname, middlename, lastname).casefold()
