"""
File Parser Service
────────────────────
Converts raw bytes (CSV / Excel / PDF) into a list of dicts.
Does NOT do any business logic — just returns clean rows.

PDF extraction uses pdfplumber. If the PDF has tables, we parse them.
If it's a narrative PDF (no tables), we return an empty list and let
the calling code decide how to handle it.

Column names are returned as-is. The AI Column Mapper normalises them.
"""

from __future__ import annotations
import io
import csv
import chardet
import pandas as pd
import pdfplumber
from core.domain.entities.upload_job import FileType


class FileParserService:
    MAX_ROWS = 50_000  # Safety cap

    def detect_file_type(self, filename: str, content: bytes) -> FileType:
        lower = filename.lower()
        if lower.endswith(".csv"):
            return FileType.CSV
        if lower.endswith((".xlsx", ".xls", ".xlsm")):
            return FileType.EXCEL
        if lower.endswith(".pdf"):
            return FileType.PDF
        # Fallback: sniff magic bytes
        if content[:4] in (b"%PDF",):
            return FileType.PDF
        if content[:2] == b"PK":  # zip-based: xlsx
            return FileType.EXCEL
        return FileType.CSV  # assume CSV

    def parse(
        self, content: bytes, filename: str
    ) -> tuple[FileType, list[dict], list[str]]:
        """
        Returns (file_type, rows, column_names).
        rows is a list of plain dicts with string values.
        """
        ftype = self.detect_file_type(filename, content)

        if ftype == FileType.CSV:
            rows, cols = self._parse_csv(content)
        elif ftype == FileType.EXCEL:
            rows, cols = self._parse_excel(content)
        elif ftype == FileType.PDF:
            rows, cols = self._parse_pdf(content)
        else:
            rows, cols = [], []

        # Cap rows
        rows = rows[: self.MAX_ROWS]
        return ftype, rows, cols

    # ── Parsers ───────────────────────────────────────────────────

    def _parse_csv(self, content: bytes) -> tuple[list[dict], list[str]]:
        # Auto-detect encoding
        detected = chardet.detect(content)
        encoding = detected.get("encoding") or "utf-8"
        try:
            text = content.decode(encoding, errors="replace")
        except Exception:
            text = content.decode("utf-8", errors="replace")

        # Sniff delimiter
        sample = text[:4096]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel  # default to comma

        reader = csv.DictReader(io.StringIO(text), dialect=dialect)
        rows = [dict(row) for row in reader]
        cols = list(reader.fieldnames or [])
        return rows, cols

    # In file_parser.py
    def _parse_excel(self, content: bytes) -> tuple[list[dict], list[str]]:
        buf = io.BytesIO(content)
        xl = pd.ExcelFile(buf)
        for sheet in xl.sheet_names:
            # 1. Read as strings to avoid initial NaN float conversion
            df = xl.parse(sheet, dtype=str) 
            df = df.dropna(how="all")
            df.columns = [str(c).strip() for c in df.columns]
            
            if len(df) > 0:
                # 2. Explicitly replace both actual NaN and string "nan" with None
                df = df.replace(["nan", "NaN", "NAN"], None)
                rows = df.where(pd.notna(df), None).to_dict(orient="records")
                return rows, list(df.columns)
        return [], []

    def _parse_pdf(self, content: bytes) -> tuple[list[dict], list[str]]:
        buf = io.BytesIO(content)
        all_rows: list[dict] = []
        col_names: list[str] = []

        with pdfplumber.open(buf) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()
                for table in tables:
                    if not table or len(table) < 2:
                        continue
                    # First row = headers
                    headers = [
                        str(h).strip() if h else f"col_{i}"
                        for i, h in enumerate(table[0])
                    ]
                    if not col_names:
                        col_names = headers

                    for row in table[1:]:
                        if not any(row):
                            continue
                        record = {
                            headers[i]: (str(cell).strip() if cell else "")
                            for i, cell in enumerate(row)
                            if i < len(headers)
                        }
                        all_rows.append(record)

        return all_rows, col_names