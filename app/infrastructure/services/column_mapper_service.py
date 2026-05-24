"""
Column Mapper Service
──────────────────────
Sends column names + 3 sample rows to the LLM.
The LLM returns a JSON mapping of raw column → standard field.

This is the key to handling HP car files, money lender files,
furniture HP files, phone HP files, etc. without hardcoding column names.

Standard fields (target schema):
  client_name, phone_number, national_id,
  asset_identifier, asset_description, tracking_identifier,
  total_principal, total_payable, amount_due, installment_amount,
  overdue_amount, penalty_amount,
  contract_start_date, contract_end_date, due_date, last_payment_date

If a column doesn't map to anything, tag it "ignore".
"""

from __future__ import annotations
import json
import httpx
from config import get_settings

import os
import sys
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(current_dir))))
sys.path.append(parent_dir)

from utils.logger import logger


SYSTEM_PROMPT = """\
You are a data schema expert. An officer has uploaded a client list file.
Your job is to map the file's raw column names to our standard schema.

Standard schema fields:
- client_name         : Full name of the client/debtor
- phone_number        : Phone number (mobile)
- national_id         : National ID / passport number
- asset_identifier    : Vehicle plate number/Registration number, loan account number, device serial (what identifies the asset)
- asset_description   : Human-readable asset label — car model, phone model, appliance name (e.g. "TOYOTA HILUX", "iPhone 14", "LG Fridge")
- tracking_identifier : Chassis number, GPS tracker ID, IMEI, device serial for tracking purposes
- product_type        : The type of product being financed. ALWAYS include this key — infer it 
                        from context, do not map a raw column to it. 
                        Values: "car_hp", "phone_hp", "loan", "furniture_hp", "motorcycle_hp", "goods_hp".
                        Example: file has RegNo + Chasis + Model → "product_type": "car_hp"
- total_principal     : Original loan / hire purchase amount (before interest)
- total_payable       : Total amount payable including all interest over the contract
- amount_due          : The current installment or amount due right now (this cycle)
- installment_amount  : The standard periodic installment amount (same as amount due)
- overdue_amount      : Accumulated past-due balance — money the client has already missed paying
- penalty_amount      : Late payment penalties or fees charged separately (NOT the same as overdue balance)
- contract_start_date : Date the contract / loan / HP agreement started (sale date, disbursement date)
- contract_end_date   : Date the final payment is expected / contract closes
- due_date            : The date the current payment is due or overdue by (same as instalment date)
- last_payment_date   : Date the client last made a payment

CRITICAL FINANCIAL DISTINCTIONS:
- "OverDue" or "Arrears" columns = overdue_amount (missed past payments, NOT current amount due)
- "Due" or "Instalment" or "Monthly" columns = amount_due (what's due this cycle)
- "Penalty" or "Fine" or "Late Fee" = penalty_amount (separate charge, not the balance)
- "Balance" or "Outstanding" = amount_due (total remaining balance)

Rules:
1. Return ONLY valid JSON: { "raw_column_name": "standard_field_or_ignore" }
2. Map to "ignore" for columns that don't match any standard field.
3. CRITICAL: "Model", "Make", "Description", "Item" → asset_description (NOT ignored)
4. CRITICAL: Plate/Registration → asset_identifier. Chassis/IMEI/GPS ID → tracking_identifier.
5. due_date and installment_date are the SAME thing — map both to "due_date".
6. Determine the product type from context and return it as "_inferred_product_type" at the root (e.g. "car_hp", "phone_hp", "loan", "furniture_hp", "motorcycle_hp").
"""

# Hardcoded fallback for vehicle HP files (used when LLM returns empty/garbage)
VEHICLE_HP_FALLBACK: dict[str, str] = {
    "Client":      "client_name",
    "Telephone":   "phone_number",
    "RegNo":       "asset_identifier",
    "Model":       "asset_description",
    "Chasis":      "tracking_identifier",
    "DateDue":     "due_date",
    "DateOfSale":  "contract_start_date",
    "EndDate":     "contract_end_date",
    "LastPaid":    "last_payment_date",
    "OverDue":     "overdue_amount",
    "Due":         "amount_due",
    "Penalty":     "penalty_amount",
    "Comments":    "ignore",
    "Source":      "ignore",
    "_inferred_product_type": "car_hp",
}


class ColumnMapperService:
    def __init__(self) -> None:
        self._settings = get_settings()

    async def map_columns(
        self,
        column_names: list[str],
        sample_rows: list[dict],
        filename: str = "",
    ) -> dict[str, str]:
        # Safe fallback from the start
        mapping: dict[str, str] = {col: "ignore" for col in column_names}
        mapping["_inferred_product_type"] = "unknown"

        try:
            clean_samples = [
                {k: v for k, v in row.items() if v is not None and str(v).strip() not in ("", "nan", "NaN")}
                for row in sample_rows[:3]
            ]
            sample_json = json.dumps(clean_samples, default=str, indent=2)

            user_msg = (
                f"File: {filename}\n"
                f"Columns: {json.dumps(column_names)}\n"
                f"Sample rows (max 3):\n{sample_json}\n\n"
                "Return ONLY valid JSON. You MUST include \"_inferred_product_type\" as a root key."
            )

            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    f"{self._settings.openrouter_base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self._settings.openrouter_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self._settings.openrouter_model,
                        "max_tokens": 800,
                        "messages": [
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": user_msg},
                        ],
                        "temperature": 0,
                    },
                )
                response.raise_for_status()
                data = response.json()

            content = data["choices"][0]["message"]["content"]
            raw_text = (content or "").strip()
            logger.warning("column_mapper_raw", raw=repr(raw_text[:500]))

            # Strip markdown fences before looking for JSON
            if "```" in raw_text:
                fence_start = raw_text.find("```")
                inner = raw_text[fence_start + 3:]
                if inner.startswith("json"):
                    inner = inner[4:]
                fence_end = inner.find("```")
                raw_text = inner[:fence_end].strip() if fence_end != -1 else inner.strip()

            # Strip prose preamble — find the first '{'
            brace_idx = raw_text.find("{")
            if brace_idx > 0:
                raw_text = raw_text[brace_idx:]
            raw_text = raw_text.strip()

            # Brace-counting to find the true closing '}' — rfind is unsafe when
            # the LLM's trailing note contains its own braces.
            depth, brace_end, in_string, escape_next = 0, -1, False, False
            for i, ch in enumerate(raw_text):
                if escape_next:
                    escape_next = False; continue
                if ch == "\\" and in_string:
                    escape_next = True; continue
                if ch == '"':
                    in_string = not in_string; continue
                if in_string:
                    continue
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        brace_end = i; break
            if brace_end != -1:
                raw_text = raw_text[:brace_end + 1]

            parsed = json.loads(raw_text)
            mapping = parsed

        except Exception as exc:
            logger.warning("column_mapper_llm_failed", error=str(exc), columns=column_names)

        # If everything is still ignored, use hardcoded fallback
        real_cols = [k for k in mapping if not k.startswith("_")]
        ignored_count = sum(1 for k in real_cols if mapping[k] == "ignore")

        if ignored_count == len(real_cols):
            logger.warning("column_mapper_using_hardcoded_fallback", columns=column_names)
            for col in column_names:
                if col in VEHICLE_HP_FALLBACK:
                    mapping[col] = VEHICLE_HP_FALLBACK[col]
            mapping["_inferred_product_type"] = "car_hp"

        return mapping

    def apply_mapping(
        self,
        rows: list[dict],
        mapping: dict[str, str],
    ) -> list[dict]:
        """
        Converts raw rows into normalised dicts using the AI mapping.
        Columns mapped to "ignore" go into "extras" for raw_data audit.
        """
        inferred_product_type = mapping.get("_inferred_product_type", "unknown")

        normalised = []
        for row in rows:
            record: dict = {"extras": {}, "product_type": inferred_product_type}
            for raw_col, value in row.items():
                if raw_col == "_inferred_product_type":
                    continue
                standard = mapping.get(raw_col, "ignore")
                if standard == "ignore":
                    record["extras"][raw_col] = value
                else:
                    record[standard] = value
            normalised.append(record)
        return normalised