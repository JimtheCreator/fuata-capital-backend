"""
Data Cleaner Service
─────────────────────
Takes normalised dicts from the column mapper and produces
clean Client domain entities.

Phone: converts 07xx → +2547xx, validates via phonenumbers lib.
Dates: arrow parses any sane format.
Amounts: strips KES, commas, spaces → float.
Priority: computed from due_date vs today.
"""

from __future__ import annotations
import re
from datetime import date, datetime
from typing import Any

import arrow
import phonenumbers

from core.domain.entities.client import Client, PriorityTier, ClientStatus
import math

_AMOUNT_STRIP = re.compile(r"[^\d.]")
_KENYA_CODE = "KE"


class DataCleanerService:
    def clean_rows(
        self,
        normalised_rows: list[dict],
        officer_id: str,
        job_id: str,
    ) -> tuple[list[Client], list[dict]]:
        """
        Returns (valid_clients, failed_rows).
        """
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
        """
        Recursively replace float NaN / Inf with None so the dict
        is always JSON-serialisable before we hand it to Supabase.
        """
        out = {}
        for k, v in d.items():
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                out[k] = None
            elif isinstance(v, dict):
                out[k] = DataCleanerService._sanitize_dict(v)
            else:
                out[k] = v
        return out

    def _to_client(
        self, row: dict, officer_id: str, job_id: str
    ) -> Client:
        c = Client(officer_id=officer_id, job_id=job_id)
        c.raw_data = self._sanitize_dict(dict(row))  # Preserve original, NaN-safe

        c.client_name = str(row.get("client_name") or "").strip()
        c.phone_number = self._clean_phone(str(row.get("phone_number") or ""))
        c.national_id = str(row.get("national_id") or "").strip()
        c.product_type = self._clean_product_type(
            str(row.get("product_type") or "")
        )

        c.total_principal = self._clean_amount(row.get("total_principal"))
        c.amount_due = self._clean_amount(row.get("amount_due"))
        c.installment_amount = self._clean_amount(row.get("installment_amount"))

        c.due_date = self._clean_date(row.get("due_date"))
        c.installment_date = self._clean_date(row.get("installment_date"))
        c.last_payment_date = self._clean_date(row.get("last_payment_date"))

        c.status = self._clean_status(str(row.get("status") or ""))

        # Derive priority
        c.priority_tier = c.compute_priority()

        return c

    # ── Cleaners ──────────────────────────────────────────────────

    def _clean_phone(self, raw: str) -> str:
        if not raw:
            return ""
        raw = raw.strip().replace(" ", "").replace("-", "")
        # Already international
        if raw.startswith("+"):
            try:
                parsed = phonenumbers.parse(raw)
                if phonenumbers.is_valid_number(parsed):
                    return phonenumbers.format_number(
                        parsed,
                        phonenumbers.PhoneNumberFormat.E164,
                    )
            except Exception:
                pass
        # Kenyan local format
        if raw.startswith("07") or raw.startswith("01"):
            raw = "+254" + raw[1:]
        elif raw.startswith("7") and len(raw) == 9:
            raw = "+254" + raw
        try:
            parsed = phonenumbers.parse(raw, _KENYA_CODE)
            if phonenumbers.is_valid_number(parsed):
                return phonenumbers.format_number(
                    parsed,
                    phonenumbers.PhoneNumberFormat.E164,
                )
        except Exception:
            pass
        return raw  # Return as-is if we can't parse — don't lose the data

    def _clean_amount(self, raw: Any) -> float:
        if raw is None or str(raw).strip() in ("", "None", "NaN", "nan"):
            return 0.0
        try:
            # Strip currency and commas
            stripped = _AMOUNT_STRIP.sub("", str(raw))
            if not stripped:
                return 0.0
            
            val = float(stripped)
            
            # CRITICAL: Check if the resulting float is NaN
            if math.isnan(val):
                return 0.0
                
            return val
        except (ValueError, TypeError):
            return 0.0

    def _clean_date(self, raw: Any) -> date | None:
        if raw is None:
            return None

        # pandas Timestamp / NaT comes straight from pd.read_excel().
        # Must be checked before isinstance(datetime) because Timestamp
        # is a subclass of datetime.
        try:
            import pandas as pd
            if isinstance(raw, pd.Timestamp):
                return None if pd.isna(raw) else raw.date()
        except ImportError:
            pass

        # Plain Python datetime / date objects
        if isinstance(raw, datetime):
            return raw.date()
        if isinstance(raw, date):
            return raw

        # String fallback — covers "NaT", "NaN", empty, or any text format
        s = str(raw).strip()
        if s in ("", "None", "NaN", "nan", "NaT"):
            return None
        # arrow only handles YYYY-first formats; Kenyan files commonly use
        # DD/MM/YYYY (e.g. "02/03/2026"). dateutil with dayfirst=True handles
        # both unambiguously and falls back gracefully on ISO strings too.
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
        if any(k in raw for k in ("goods", "appliance")):
            return "goods_hp"
        return raw or "unknown"

    def _clean_status(self, raw: str) -> ClientStatus:
        raw = raw.lower().strip()
        if any(k in raw for k in ("settle", "paid", "clear", "closed")):
            return ClientStatus.SETTLED
        if any(k in raw for k in ("default", "bad", "npa", "loss")):
            return ClientStatus.DEFAULTED
        if any(k in raw for k in ("active", "current", "open", "normal")):
            return ClientStatus.ACTIVE
        return ClientStatus.UNKNOWN