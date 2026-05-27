"""
core/services/kill_list_evaluator.py
─────────────────────────────────────────
Domain rule engine evaluating client portfolios against the structural Triad profiles.
"""
from __future__ import annotations
from datetime import date, timedelta
from typing import Any
import structlog

from core.domain.entities.client import Client, PriorityTier, ClientStatus
from core.domain.entities.ptp import PTPStatus

log = structlog.get_logger(__name__)

class KillListEvaluatorService:
    @staticmethod
    def extract_actionable_targets(clients: list[Client], active_ptps: list[Any], today: date) -> dict[str, list[Client]]:
        """
        Processes a portfolio and returns only clients meeting the strict Triad metrics,
        grouped by priority tier matching presentation expectations.
        """
        # Map active PTP states for ultra-fast lookup complexity: O(N)
        ptp_map = {p["client_id"]: p for p in active_ptps}
        
        grouped_buckets: dict[str, list[Client]] = {
            PriorityTier.OVERDUE.value: [],
            PriorityTier.DUE_TOMORROW.value: [],
            PriorityTier.DUE_THIS_WEEK.value: []
        }

        count_filtered = 0

        for client in clients:
            # Rule 0: Skip completely settled clients
            if client.status == ClientStatus.SETTLED:
                continue

            client_ptp = ptp_map.get(client.id)
            is_serial_defaulter = client.raw_data.get("Penalty", 0) != 0 and float(str(client.raw_data.get("Penalty")).replace(",", "")) > 25000

            # ── Pillar 1: Broken Promises (Highest Urgency Escalation) ──
            if client_ptp and client_ptp["promised_date"] < today and client_ptp["status"] == PTPStatus.PENDING.value:
                client.priority_tier = PriorityTier.OVERDUE
                # Inject a structural flag so the AI strategy service prompt notices the broken promise context
                client.raw_data["_internal_context"] = "BROKEN_PTP"
                client.raw_data["_ptp_date"] = client_ptp["promised_date"].isoformat()
                grouped_buckets[PriorityTier.OVERDUE.value].append(client)
                continue

            # ── Pillar 2: Active Promises Due Today ──
            if client_ptp and client_ptp["promised_date"] == today and client_ptp["status"] == PTPStatus.PENDING.value:
                client.priority_tier = PriorityTier.DUE_TOMORROW
                client.raw_data["_internal_context"] = "PTP_TODAY"
                grouped_buckets[PriorityTier.DUE_TOMORROW.value].append(client)
                continue

            # ── Pillar 3: Core Arrears or Approaching Cycles ──
            if client.due_date:
                days_until_due = (client.due_date - today).days

                # Overdue Accounts: date is past AND client has actual accumulated arrears.
                # NOTE: amount_due is the installment size — always > 0 for active clients —
                #       so it cannot be used as an arrears signal. overdue_amount is the
                #       explicit arrears field from the source data.
                if days_until_due < 0 and client.overdue_amount > 0:
                    client.priority_tier = PriorityTier.OVERDUE
                    client.days_overdue = abs(days_until_due)
                    grouped_buckets[PriorityTier.OVERDUE.value].append(client)

                # Due Tomorrow: genuinely upcoming installment with a real outstanding balance.
                elif days_until_due == 1 and client.amount_due > 0:
                    client.priority_tier = PriorityTier.DUE_TOMORROW
                    grouped_buckets[PriorityTier.DUE_TOMORROW.value].append(client)

                # High-Risk Serial Defaulters (Pulled early 3 days before cycle)
                elif days_until_due <= 3 and is_serial_defaulter and client.amount_due > 0:
                    client.priority_tier = PriorityTier.DUE_THIS_WEEK
                    client.raw_data["_internal_context"] = "PREVENTATIVE_HIGH_RISK"
                    grouped_buckets[PriorityTier.DUE_THIS_WEEK.value].append(client)

                else:
                    count_filtered += 1
            else:
                # No due date but explicit arrears → flag them.
                if client.overdue_amount > 0:
                    client.priority_tier = PriorityTier.OVERDUE
                    grouped_buckets[PriorityTier.OVERDUE.value].append(client)
                else:
                    count_filtered += 1

        log.info("portfolio_triad_evaluation_complete", 
                 total_ingested=len(clients), 
                 actionable_targets=sum(len(v) for v in grouped_buckets.values()),
                 healthy_filtered_out=count_filtered)

        return grouped_buckets