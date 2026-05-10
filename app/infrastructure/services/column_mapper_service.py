"""
Column Mapper Service
──────────────────────
Sends column names + 3 sample rows to the LLM.
The LLM returns a JSON mapping of raw column → standard field.

This is the key to handling HP car files, money lender files,
furniture HP files, etc. without hardcoding any column names.

Standard fields (target schema):
  client_name, phone_number, national_id, product_type,
  total_principal, amount_due, installment_amount,
  due_date, installment_date, last_payment_date, status

If a column doesn't map to anything, it's tagged as "ignore".
"""

from __future__ import annotations
import json
import httpx
from config import get_settings


SYSTEM_PROMPT = """\
You are a data schema expert. An officer has uploaded a client list file.
Your job is to map the file's column names to our standard schema.

Standard schema fields:
- client_name       : Full name of the client/debtor
- phone_number      : Phone number (mobile)
- national_id       : ID / passport number
- product_type      : What product (car, motorcycle, furniture, loan, goods, etc.)
- total_principal   : Original loan / HP value
- amount_due        : Current outstanding balance owed
- installment_amount: Monthly/periodic installment amount
- due_date          : Date the payment is due or overdue by
- installment_date  : Date of the next scheduled installment
- last_payment_date : When they last paid
- status            : Account status (active, defaulted, settled, etc.)

Rules:
1. Return ONLY valid JSON — no explanation, no markdown, no extra text.
2. The JSON must be an object: { "raw_column_name": "standard_field_or_ignore" }
3. Map to "ignore" for columns that don't match any standard field.
4. If there are multiple columns that could map to the same standard field,
   pick the most specific one and mark the others as "ignore".
5. Infer product_type from column names or sample data if a dedicated column
   doesn't exist (e.g. if you see "Vehicle Reg" or "Chassis No", product_type = "car_hp").
6. Date columns: accept any date format — mark them correctly regardless of format.
"""


class ColumnMapperService:
    def __init__(self) -> None:
        self._settings = get_settings()

    async def map_columns(
        self,
        column_names: list[str],
        sample_rows: list[dict],
        filename: str = "",
    ) -> dict[str, str]:
        """
        Returns {raw_column: standard_field_or_ignore}
        """
        sample_json = json.dumps(sample_rows[:3], default=str, indent=2)
        user_msg = (
            f"File: {filename}\n"
            f"Columns: {json.dumps(column_names)}\n"
            f"Sample rows (max 3):\n{sample_json}\n\n"
            "Return the column mapping JSON."
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

        raw_text = data["choices"][0]["message"]["content"].strip()

        # Strip any accidental markdown fences
        if raw_text.startswith("```"):
            raw_text = raw_text.split("```")[1]
            if raw_text.startswith("json"):
                raw_text = raw_text[4:]
        raw_text = raw_text.strip()

        try:
            mapping: dict[str, str] = json.loads(raw_text)
        except json.JSONDecodeError:
            # Fallback: return empty mapping so we store raw_data only
            mapping = {col: "ignore" for col in column_names}

        return mapping

    def apply_mapping(
        self,
        rows: list[dict],
        mapping: dict[str, str],
    ) -> list[dict]:
        """
        Converts raw rows into normalised dicts using the AI mapping.
        Unknown / unmapped columns are collapsed into a 'extras' key.
        """
        normalised = []
        for row in rows:
            record: dict = {"extras": {}}
            for raw_col, value in row.items():
                standard = mapping.get(raw_col, "ignore")
                if standard == "ignore":
                    record["extras"][raw_col] = value
                else:
                    record[standard] = value
            normalised.append(record)
        return normalised