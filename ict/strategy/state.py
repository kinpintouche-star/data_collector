from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class SetupStatus(StrEnum):
    IDLE = "IDLE"
    WAITING_LEG = "WAITING_LEG"
    WAITING_PD_ARRAY = "WAITING_PD_ARRAY"
    WAITING_MITIGATION = "WAITING_MITIGATION"
    WAITING_REJECTION = "WAITING_REJECTION"
    IN_POSITION = "IN_POSITION"
    COMPLETED = "COMPLETED"
    INVALIDATED = "INVALIDATED"


@dataclass
class SetupEventRecord:
    setup_id: str
    event_type: str
    event_time: datetime
    direction: str | None = None
    price: float | None = None
    state_before: str | None = None
    state_after: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SetupState:
    setup_id: str
    status: SetupStatus = SetupStatus.IDLE
    direction: str | None = None
    events: list[SetupEventRecord] = field(default_factory=list)

    def transition(
        self,
        status: SetupStatus,
        event_type: str,
        event_time: datetime,
        price: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SetupEventRecord:
        before = self.status
        self.status = status
        event = SetupEventRecord(
            setup_id=self.setup_id,
            event_type=event_type,
            event_time=event_time,
            direction=self.direction,
            price=price,
            state_before=before.value,
            state_after=status.value,
            metadata=metadata or {},
        )
        self.events.append(event)
        return event
