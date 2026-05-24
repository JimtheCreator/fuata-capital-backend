"""
Data Cleaner Service
─────────────────────
Takes normalised dicts from the column mapper and produces
clean Client domain entities.

Phone: converts 07xx → +2547xx, validates via phonenumbers lib.
Dates: dateutil parses any sane format.
Amounts: strips KES / commas / spaces → float.
Priority: computed from due_date vs today.
"""

from __future__ import annotations
import re
from datetime import date, datetime
from typing import Any
import math

import phonenumbers
from core.domain.entities.client import Client, PriorityTier

_AMOUNT_STRIP = re.compile(r"[^\d.]")
_KENYA_CODE = "KE"


class DataCleanerService:
    def clean_rows(
        self,
        normalised_rows: list[dict],
        officer_id: str,
        job_id: str,
    ) -> tuple[list[Client], list[dict]]:
        """Returns (valid_clients, failed_rows)."""
        clients: list[Client] = []
        failed: list[dict] = []

        for row in normalised_rows:
            try:
                c = self._to_client(row, officer_id, job_id)
                clients.append(c)
            except Exception as exc:
                failed.append({"row": row, "error": str(exc)})

        return clients, failed

    @staticmethod
    def _sanitize_dict(d: dict) -> dict:
        out = {}
        for k, v in d.items():
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                out[k] = None
            elif isinstance(v, dict):
                out[k] = DataCleanerService._sanitize_dict(v)
            else:
                out[k] = v
        return out

    def _to_client(self, row: dict, officer_id: str, job_id: str) -> Client:
        c = Client(officer_id=officer_id, job_id=job_id)
        c.raw_data = self._sanitize_dict(dict(row))

        # ── Identity ──────────────────────────────────────────────
        c.client_name = str(row.get("client_name") or "").strip()
        c.phone_number = self._clean_phone(str(row.get("phone_number") or ""))
        c.national_id = str(row.get("national_id") or "").strip()
        c.product_type = self._clean_product_type(str(row.get("product_type") or ""))

        # ── Asset ─────────────────────────────────────────────────
        c.asset_identifier = str(row.get("asset_identifier") or "").strip()
        c.asset_description = str(row.get("asset_description") or "").strip()
        c.tracking_identifier = str(row.get("tracking_identifier") or "").strip()

        # ── Financial ─────────────────────────────────────────────
        c.total_principal = self._clean_amount(row.get("total_principal"))
        c.total_payable = self._clean_amount(row.get("total_payable"))
        c.amount_due = self._clean_amount(row.get("amount_due"))
        c.installment_amount = self._clean_amount(row.get("installment_amount"))
        c.overdue_amount = self._clean_amount(row.get("overdue_amount"))
        c.penalty_amount = self._clean_amount(row.get("penalty_amount"))

        # ── Timeline ──────────────────────────────────────────────
        c.contract_start_date = self._clean_date(row.get("contract_start_date"))
        c.contract_end_date = self._clean_date(row.get("contract_end_date"))
        c.due_date = self._clean_date(row.get("due_date"))
        # installment_date == due_date — no separate field
        c.last_payment_date = self._clean_date(row.get("last_payment_date"))

        # ── Derive priority ───────────────────────────────────────
        c.priority_tier = c.compute_priority()

        return c

    # ── Cleaners ──────────────────────────────────────────────────

    def _clean_phone(self, raw: str) -> str:
        if not raw:
            return ""
        # Take only the first number if multiple are comma/space separated
        raw = raw.split(",")[0].strip()
        raw = raw.replace(" ", "").replace("-", "")
        if raw.startswith("+"):
            try:
                parsed = phonenumbers.parse(raw)
                if phonenumbers.is_valid_number(parsed):
                    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
            except Exception:
                pass
        if raw.startswith("07") or raw.startswith("01"):
            raw = "+254" + raw[1:]
        elif raw.startswith("7") and len(raw) == 9:
            raw = "+254" + raw
        try:
            parsed = phonenumbers.parse(raw, _KENYA_CODE)
            if phonenumbers.is_valid_number(parsed):
                return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        except Exception:
            pass
        return raw

    def _clean_amount(self, raw: Any) -> float:
        if raw is None or str(raw).strip() in ("", "None", "NaN", "nan"):
            return 0.0
        try:
            # Handle negative values (e.g. "-500" means overpaid / credit)
            s = str(raw).strip()
            negative = s.startswith("-")
            stripped = _AMOUNT_STRIP.sub("", s)
            if not stripped:
                return 0.0
            val = float(stripped)
            if math.isnan(val):
                return 0.0
            return -val if negative else val
        except (ValueError, TypeError):
            return 0.0

    def _clean_date(self, raw: Any) -> date | None:
        if raw is None:
            return None
        try:
            import pandas as pd
            if isinstance(raw, pd.Timestamp):
                return None if pd.isna(raw) else raw.date()
        except ImportError:
            pass

        if isinstance(raw, datetime):
            return raw.date()
        if isinstance(raw, date):
            return raw

        s = str(raw).strip()
        if s in ("", "None", "NaN", "nan", "NaT"):
            return None
        try:
            from dateutil import parser as dparser
            return dparser.parse(s, dayfirst=True).date()
        except Exception:
            return None

    def _clean_product_type(self, raw: str) -> str:
        raw = raw.lower().strip()
        if any(k in raw for k in ("car", "vehicle", "auto", "motor", "hp")):
            return "car_hp"
        if any(k in raw for k in ("furniture", "sofa", "bed", "fridge")):
            return "furniture_hp"
        if any(k in raw for k in ("loan", "lend", "credit", "mikopo")):
            return "loan"
        if any(k in raw for k in ("bike", "motorcycle", "boda")):
            return "motorcycle_hp"
        if any(k in raw for k in ("phone", "iphone", "samsung", "tecno", "itel")):
            return "phone_hp"
        if any(k in raw for k in ("goods", "appliance")):
            return "goods_hp"
        return raw or "unknown"