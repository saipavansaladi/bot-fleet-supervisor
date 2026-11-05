"""
State Synchronization & Broker Reconciliation Engine
====================================================

Implements the single-source-of-truth reconciliation pattern:
"The broker's database represents physical truth; local memory or disk cache
is merely an untrusted draft."

When a bot initializes, restarts, or recovers from a disconnect:
1. It queries the broker for current live positions and orders.
2. It compares the broker's reality against its internal local state.
3. It identifies, categorizes, and logs every discrepancy.
4. It adopts the broker's version as canonical truth before processing market ticks.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import logging
from typing import Dict, List, Tuple
from fake_broker import FakeBroker, Position

logger = logging.getLogger("reconciler")


class DiscrepancyType(str, Enum):
    """Categorization of state discrepancies between local memory and broker."""
    GHOST_POSITION = "GHOST_POSITION"          # In local state, but missing at broker
    UNTRACKED_POSITION = "UNTRACKED_POSITION"  # At broker, but missing in local state
    QTY_MISMATCH = "QTY_MISMATCH"              # Quantities disagree
    PRICE_MISMATCH = "PRICE_MISMATCH"          # Cost basis disagrees


@dataclass
class Discrepancy:
    """Detailed audit record of an individual reconciliation mismatch."""
    bot_id: str
    symbol: str
    discrepancy_type: DiscrepancyType
    local_qty: float
    broker_qty: float
    message: str


@dataclass
class ReconciliationReport:
    """The complete outcome of a reconciliation pass."""
    bot_id: str
    is_clean: bool
    discrepancies: List[Discrepancy]
    reconciled_positions: Dict[str, Position]


class Reconciler:
    """
    Compares local bot state against broker state and enforces convergence.
    """

    @staticmethod
    def reconcile(
        bot_id: str,
        local_positions: Dict[str, float],
        broker: FakeBroker,
    ) -> ReconciliationReport:
        """
        Reconcile local positions dictionary against live broker positions.

        Parameters
        ----------
        bot_id : str
            Identifier of the bot undergoing reconciliation.
        local_positions : dict of {symbol: qty}
            The bot's internal, potentially stale, position mapping.
        broker : FakeBroker
            Live broker adapter representing external ground truth.

        Returns
        -------
        ReconciliationReport
            Report containing discrepancies and the authoritative positions dictionary.
        """
        broker_positions = broker.get_positions()
        discrepancies: List[Discrepancy] = []

        all_symbols = set(local_positions.keys()).union(set(broker_positions.keys()))

        for sym in sorted(all_symbols):
            local_qty = float(local_positions.get(sym, 0.0))
            broker_pos = broker_positions.get(sym)
            broker_qty = float(broker_pos.qty) if broker_pos else 0.0

            # 1. Ghost Position: Bot thinks it owns shares, but broker shows 0
            if local_qty > 0.0 and broker_qty == 0.0:
                disc = Discrepancy(
                    bot_id=bot_id,
                    symbol=sym,
                    discrepancy_type=DiscrepancyType.GHOST_POSITION,
                    local_qty=local_qty,
                    broker_qty=0.0,
                    message=(
                        f"[{bot_id}] GHOST POSITION: Local state claims {local_qty} shares of {sym}, "
                        f"but broker reports 0 shares. (Adopting broker: 0 shares)."
                    ),
                )
                discrepancies.append(disc)
                logger.warning(disc.message)

            # 2. Untracked Position: Broker has shares, but bot local state shows 0
            elif local_qty == 0.0 and broker_qty > 0.0:
                disc = Discrepancy(
                    bot_id=bot_id,
                    symbol=sym,
                    discrepancy_type=DiscrepancyType.UNTRACKED_POSITION,
                    local_qty=0.0,
                    broker_qty=broker_qty,
                    message=(
                        f"[{bot_id}] UNTRACKED POSITION: Broker reports {broker_qty} shares of {sym}, "
                        f"but local state had 0. (Adopting broker: {broker_qty} shares)."
                    ),
                )
                discrepancies.append(disc)
                logger.warning(disc.message)

            # 3. Quantity Mismatch: Both agree position exists, but quantities differ
            elif abs(local_qty - broker_qty) > 1e-4:
                disc = Discrepancy(
                    bot_id=bot_id,
                    symbol=sym,
                    discrepancy_type=DiscrepancyType.QTY_MISMATCH,
                    local_qty=local_qty,
                    broker_qty=broker_qty,
                    message=(
                        f"[{bot_id}] QUANTITY MISMATCH: Local state has {local_qty} shares of {sym}, "
                        f"but broker reports {broker_qty} shares. Delta: {broker_qty - local_qty:+.2f}. "
                        f"(Adopting broker: {broker_qty} shares)."
                    ),
                )
                discrepancies.append(disc)
                logger.warning(disc.message)

        is_clean = len(discrepancies) == 0

        if is_clean:
            logger.info(f"[{bot_id}] State reconciliation CLEAN. Local state matches broker ground-truth.")
        else:
            logger.warning(
                f"[{bot_id}] State reconciliation completed with {len(discrepancies)} discrepancy(s). "
                f"All local state updated to match broker ground-truth."
            )

        return ReconciliationReport(
            bot_id=bot_id,
            is_clean=is_clean,
            discrepancies=discrepancies,
            reconciled_positions=broker_positions,
        )
