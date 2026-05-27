"""
AI Strategy Service
────────────────────
Takes the segmented client groups and asks the LLM to:
1. Confirm / reorder priorities.
2. Write a personalised outreach message per overdue client.
3. Return structured JSON we can persist.

We batch clients to avoid blowing the context window.
Max 50 clients per LLM call. For large lists we run multiple calls
concurrently (bounded by a semaphore).
"""

from __future__ import annotations
import asyncio
import json
from datetime import datetime, timedelta

import httpx

from config import get_settings
from core.domain.entities.client import Client, PriorityTier
from core.domain.entities.kill_list_event import KillListEvent


SYSTEM_PROMPT = """\
You are a high-performance collections strategy AI for Fuata Capital in Kenya.
Your job is to create highly effective, contextual outreach messages for overdue or upcoming-due debtor accounts.

Core Rules:
1. Tone: Professional but firm. Respectful — never threatening or abusive.
2. Language: English with optional natural Swahili phrases (e.g., "Habari", "Tafadhali").
3. Include: client name, asset/installment amount due (KES), due date, and a clear call to action.
4. Profile Additions:
   - If a client is flagged with context "BROKEN_PTP", adopt a direct, authoritative tone. Firmly remind them that they missed their committed payment promise from their recent check-in.
   - If a client is flagged with context "PTP_TODAY", adopt an encouraging follow-up tone, confirming support as they settle their balance today.
5. Messages must be under 100 characters (SMS-friendly).
6. Return ONLY valid JSON. No markdown, no conversation.

Output format:
[
  {
    "client_id": "...",
    "message": "...",
    "reasoning": "one sentence explaining why this tone/approach"
  }
]
"""


class AIStrategyService:
    BATCH_SIZE = 50
    MAX_CONCURRENT = 4  # Parallel LLM calls

    def __init__(self) -> None:
        self._settings = get_settings()
        self._semaphore = asyncio.Semaphore(self.MAX_CONCURRENT)

    async def generate_kill_list(
        self,
        grouped_clients: dict[str, list[Client]],
        officer_id: str,
        job_id: str,
    ) -> list[KillListEvent]:
        """
        Processes OVERDUE, DUE_TOMORROW, DUE_THIS_WEEK groups.
        Returns a list of KillListEvent entities.
        """
        s = self._settings

        # Schedule: tomorrow 08:45
        now = datetime.utcnow()
        # Add 3 hours for EAT offset
        local_now = now + timedelta(hours=3)
        schedule_day = (local_now + timedelta(days=1)).date()
        scheduled_at = datetime(
            schedule_day.year,
            schedule_day.month,
            schedule_day.day,
            s.kill_list_send_hour,
            s.kill_list_send_minute,
        ) - timedelta(hours=3)  # Store in UTC

        priority_order = [
            PriorityTier.OVERDUE.value,
            PriorityTier.DUE_TOMORROW.value,
            PriorityTier.DUE_THIS_WEEK.value,
        ]

        all_events: list[KillListEvent] = []
        tasks = []

        for tier in priority_order:
            clients = grouped_clients.get(tier, [])
            if not clients:
                continue
            # Split into batches
            for i in range(0, len(clients), self.BATCH_SIZE):
                batch = clients[i : i + self.BATCH_SIZE]
                tasks.append(
                    self._process_batch(
                        batch, tier, officer_id, job_id, scheduled_at
                    )
                )

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                # Log but don't crash the whole pipeline
                continue
            all_events.extend(result)

        return all_events

    async def _process_batch(
        self,
        clients: list[Client],
        tier: str,
        officer_id: str,
        job_id: str,
        scheduled_at: datetime,
    ) -> list[KillListEvent]:
        async with self._semaphore:
            payload = self._build_payload(clients, tier)
            ai_results = await self._call_llm(payload)

        # Map AI results back to KillListEvent entities
        result_map = {r["client_id"]: r for r in ai_results}
        events: list[KillListEvent] = []

        for client in clients:
            ai_item = result_map.get(client.id, {})
            event = KillListEvent(
                officer_id=officer_id,
                job_id=job_id,
                client_id=client.id,
                scheduled_at=scheduled_at,
                priority_tier=tier,
                message_body=ai_item.get("message", self._fallback_message(client)),
                ai_reasoning=ai_item.get("reasoning", ""),
            )
            events.append(event)

        return events

    def _build_payload(self, clients: list[Client], tier: str) -> str:
        items = []
        for c in clients:
            items.append({
                "client_id": c.id,
                "name": c.client_name,
                "phone": c.phone_number,
                "product": c.product_type,
                "amount_due": c.amount_due,
                "due_date": c.due_date.isoformat() if c.due_date else None,
                "days_overdue": c.days_overdue,
                "tier": tier,
            })
        return json.dumps(items, default=str)

    async def _call_llm(self, payload_json: str) -> list[dict]:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{self._settings.openrouter_base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._settings.openrouter_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self._settings.openrouter_model,
                    "max_tokens": 2048,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": payload_json},
                    ],
                    "temperature": 0.3,
                },
            )
            response.raise_for_status()
            data = response.json()

        raw = data["choices"][0]["message"]["content"].strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return []

    @staticmethod
    def _fallback_message(client: Client) -> str:
        name = client.client_name or "Customer"
        amount = f"KES {client.amount_due:,.0f}"
        if client.days_overdue > 0:
            return (
                f"Dear {name}, your account of {amount} is "
                f"{client.days_overdue} days overdue. Please contact us immediately."
            )
        return (
            f"Dear {name}, your payment of {amount} is due soon. "
            "Please ensure timely payment. Thank you."
        )