"""Generate a SIE 5 package (.si5) combining SIE 4 ledger data with binary underlag.

SIE 5 is an XML-based format packaged as a zip file:
  ledger_2024.si5 (zip)
  ├── sie5.xml          ← accounting data (XML, namespace http://www.sie.se/sie5)
  └── documents/        ← attached files referenced from the XML
      ├── Verifikation_A1.pdf
      └── ...

Specification reference: http://www.sie.se/sie5
Schema version targeted: SIE 5.0 (2019-11-18)
"""
from __future__ import annotations

import io
import os
import zipfile
from datetime import datetime
from decimal import Decimal
from typing import Optional
import xml.etree.ElementTree as ET

from .models import SIEFile
from . import underlag as underlag_module

SIE5_NS  = 'http://www.sie.se/sie5'
SIE5_TAG = f'{{{SIE5_NS}}}'      # shorthand: SIE5_TAG + 'Account' etc.

CONTENT_TYPES: dict[str, str] = {
    '.pdf':  'application/pdf',
    '.jpg':  'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.png':  'image/png',
    '.tif':  'image/tiff',
    '.tiff': 'image/tiff',
    '.xml':  'application/xml',
}


def _d(date_compact: str) -> str:
    """YYYYMMDD → YYYY-MM-DD"""
    d = date_compact
    return f'{d[:4]}-{d[4:6]}-{d[6:]}' if len(d) == 8 else d


def _amt(v: Decimal) -> str:
    return f'{v:.2f}'


def _account_type(number: str, ktyp: Optional[str]) -> str:
    if ktyp == 'T':
        return 'Asset'
    if ktyp == 'I':
        return 'Income'
    if ktyp == 'K':
        return 'Cost'
    if ktyp == 'S':
        return 'Liability'
    if number.isdigit():
        n = int(number)
        if 2000 <= n < 2100:
            return 'Equity'
        if 2100 <= n < 3000:
            return 'Liability'
    return 'Asset'


def _sub(parent: ET.Element, tag: str, **attrs) -> ET.Element:
    return ET.SubElement(parent, SIE5_TAG + tag, **attrs)


def _build_xml(
    sie: SIEFile,
    doc_refs: list[tuple[int, str]],   # [(doc_id, filename), ...]
    voucher_docs: dict[tuple[str,int], list[int]],  # (series,num) → [doc_id,...]
) -> bytes:
    ET.register_namespace('', SIE5_NS)
    root = ET.Element(SIE5_TAG + 'SIEDocument')

    # ── FileInfo ──────────────────────────────────────────────────────────
    fi = _sub(root, 'FileInfo')
    _sub(fi, 'SoftwareProduct', Name=sie.program, Version=sie.program_version)
    _sub(fi, 'FileCreation',
         Time=datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
         By=sie.gen_author)

    # Parse zip_city into PostalCode + City as best we can
    zip_parts = sie.zip_city.split(None, 1) if sie.zip_city else ['', '']
    postal = zip_parts[0] if zip_parts else ''
    city   = zip_parts[1] if len(zip_parts) > 1 else sie.zip_city

    co = _sub(fi, 'Company',
              OrganizationId=sie.org_nr.replace('-', ''),
              Name=sie.company_name)
    _sub(co, 'Address',
         Street=sie.street,
         PostalCode=postal,
         City=city,
         Country='SE')

    # ── FiscalYears ───────────────────────────────────────────────────────
    fy = _sub(root, 'FiscalYears')
    _sub(fy, 'FiscalYear',
         Primary='true',
         Start=_d(sie.year_begins),
         End=_d(sie.year_ends),
         AccountingCurrency=sie.currency)

    # ── AccountingPlan ────────────────────────────────────────────────────
    plan = _sub(root, 'AccountingPlan')
    for acc in sie.accounts:
        acc_el = _sub(plan, 'Account',
                      Id=acc.number,
                      Name=acc.label,
                      Type=_account_type(acc.number, acc.ktyp))
        if acc.number in sie.ib:
            _sub(acc_el, 'OpeningBalance', amount=_amt(sie.ib[acc.number]))
        if acc.number in sie.ub:
            _sub(acc_el, 'ClosingBalance', amount=_amt(sie.ub[acc.number]))

    # ── Journals ──────────────────────────────────────────────────────────
    journals_el = _sub(root, 'Journals')

    series_map: dict[str, list] = {}
    for v in sie.vouchers:
        series_map.setdefault(v.series, []).append(v)

    for series_id, vouchers in sorted(series_map.items()):
        j = _sub(journals_el, 'Journal', Id=series_id, Name='')
        for v in vouchers:
            entry_attrs: dict[str, str] = {
                'Id':          str(v.number),
                'JournalDate': _d(v.date),
                'Text':        v.label,
            }
            if v.reg_date:
                entry_attrs['OriginalEntryDate'] = _d(v.reg_date)
            if v.signature:
                entry_attrs['CreatedBy'] = v.signature

            entry = _sub(j, 'JournalEntry', **entry_attrs)

            for t in v.transactions:
                t_attrs: dict[str, str] = {
                    'AccountId': t.account,
                    'Amount':    _amt(t.amount),
                }
                if t.label:
                    t_attrs['Text'] = t.label
                _sub(entry, 'LedgerEntry', **t_attrs)

            for doc_id in voucher_docs.get((v.series, v.number), []):
                _sub(entry, 'DocumentReference', DocumentId=str(doc_id))

    # ── Documents ─────────────────────────────────────────────────────────
    if doc_refs:
        docs_el = _sub(root, 'Documents')
        for doc_id, filename in doc_refs:
            ext  = os.path.splitext(filename)[1].lower()
            ctype = CONTENT_TYPES.get(ext, 'application/octet-stream')
            _sub(docs_el, 'Document',
                 Id=str(doc_id),
                 Name=filename,
                 ContentType=ctype)

    ET.indent(root, space='  ')
    buf = io.BytesIO()
    ET.ElementTree(root).write(buf, encoding='utf-8', xml_declaration=True)
    return buf.getvalue()


def generate_sie5(
    sie: SIEFile,
    ledger_path: str,
    output_path: str,
) -> tuple[int, int]:
    """Write a SIE 5 zip package to output_path.

    Returns (n_vouchers, n_documents).
    """
    # ── collect underlag ──────────────────────────────────────────────────
    underlag_dir, _ = underlag_module._paths(ledger_path)

    doc_counter  = 0
    doc_refs: list[tuple[int, str]] = []            # (doc_id, filename)
    voucher_docs: dict[tuple[str,int], list[int]] = {}

    for summary in underlag_module.list_all(ledger_path):
        series, number = summary['series'], summary['number']
        for finfo in underlag_module.list_for_voucher(ledger_path, series, number):
            fpath = os.path.join(underlag_dir, finfo['filename'])
            if os.path.exists(fpath):
                doc_counter += 1
                doc_refs.append((doc_counter, finfo['filename']))
                voucher_docs.setdefault((series, number), []).append(doc_counter)

    # ── build XML then zip ────────────────────────────────────────────────
    xml_bytes = _build_xml(sie, doc_refs, voucher_docs)

    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('sie5.xml', xml_bytes)
        for _doc_id, filename in doc_refs:
            fpath = os.path.join(underlag_dir, filename)
            zf.write(fpath, f'documents/{filename}')

    return len(sie.vouchers), len(doc_refs)
