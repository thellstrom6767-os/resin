# bokforing — Claude Code guidance

## Project overview

CLI accounting application for Retsina Consulting AB (Swedish AB).
Primary storage is SIE 4 plain-text ledger files (`*.se`, CP437 encoded).
Companion stores: underlag directory + SQLite database for binary supporting
documents, and SIE 5 zip packages for archiving/exchange.

## Documentation rule

**Whenever you add, remove, or change a CLI command, or change the storage
format, you must update the relevant documentation before committing:**

- `Documentation/command_reference.rst` — command options, behaviour,
  examples, and workflow snippets.
- `Documentation/storage_format.rst` — file formats, SQLite schema,
  SIE 5 XML structure, year-transition behaviour, data integrity notes.

Both files must stay in sync with the code. Never commit a code change that
affects the CLI surface or the storage format without a corresponding doc
update in the same commit.

## Project structure

```
bokforing/
    __init__.py
    models.py       — dataclasses: SIEFile, Voucher, Transaction, Account
    sie.py          — SIE 4 parser and writer (CP437, append-only)
    ledger.py       — balance computation, account lookup, year init
    reports.py      — Resultatrapport and Balansrapport ODS generators
    sie5.py         — SIE 5 export (generate_sie5) and import (restore_from_sie5)
    underlag.py     — binary document store (directory + SQLite)
    cli.py          — Click CLI: all commands
main.py             — entry point
Documentation/
    command_reference.rst
    storage_format.rst
requirements.txt    — click>=8.0, odfpy>=1.4
```

## Encoding

SIE 4 files are always written and read with `encoding='cp437',
errors='replace'`. Never change this to UTF-8 — CP437 is mandated by
the SIE standard.

## Sign convention

SIE uses standard double-entry signs throughout:

- Asset accounts (1xxx): positive = debit = the asset has a value.
- Liability/equity (2xxx): negative = credit = the liability exists.
- Income (3xxx): negative = credit = income earned.
- Expense (4xxx–8xxx): positive = debit = cost incurred.

The `reports.py` generators **negate** P&L amounts for display (income →
positive, costs → negative). The `balansrapport` preserves SIE signs as-is.
Do not change either convention without updating both the code and the
storage_format.rst sign-convention sections.

## Atomic writes

`sie.append_voucher` must remain atomic: write to `.se.tmp`, then
`os.replace`. Never append directly to the live file.

## Key constraints

- `ledger.init_from_previous` must only carry forward accounts 1000–2999.
  Income/expense accounts (3000–8999) always reset to zero each year.
- `underlag.py` naming: single file → `Verifikation_A{n}.{ext}`, multiple
  → `Verifikation_A{n}[{i}av{total}].{ext}`. Renaming on total change is
  handled by `_rename_to_reflect_total`; preserve this behaviour.
- SIE 5 round-trip: SRU codes are not carried. Document this whenever
  sie5import is described or modified.
