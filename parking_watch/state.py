from dataclasses import dataclass


@dataclass(frozen=True)
class Event:
    kind: str
    state: str


class Monitor:
    """Confirm continuous observations; absence of a frame never means free."""

    def __init__(self, free_seconds=10, occupied_seconds=3, unavailable_seconds=15,
                 max_gap_seconds=5, min_samples=3):
        self.free_seconds = free_seconds
        self.occupied_seconds = occupied_seconds
        self.unavailable_seconds = unavailable_seconds
        self.max_gap = max_gap_seconds
        self.min_samples = min_samples
        self.last_confirmed = None
        self.current = "unknown"
        self.pending = None
        self.pending_since = None
        self.samples = 0
        self.uncertain_since = None
        self.unavailable_reported = False
        self.previous_time = None
        self.interrupted = False

    def observe(self, state, now):
        if state not in {"free", "occupied", "unknown", "offline"}:
            raise ValueError("Invalid observation")
        if self.previous_time is not None:
            if now < self.previous_time:
                raise ValueError("Observation times must increase")
            if now - self.previous_time > self.max_gap:
                self.pending = None
                self.interrupted = True
        self.previous_time = now
        if state in {"unknown", "offline"}:
            self.current = state
            self.pending = None
            self.samples = 0
            self.interrupted = True
            if self.uncertain_since is None:
                self.uncertain_since = now
            if now - self.uncertain_since >= self.unavailable_seconds and not self.unavailable_reported:
                self.unavailable_reported = True
                return Event("unavailable", state)
            return None
        if state != self.pending:
            self.pending, self.pending_since, self.samples = state, now, 1
        else:
            self.samples += 1
        required = self.free_seconds if state == "free" else self.occupied_seconds
        if self.samples < self.min_samples or now - self.pending_since < required:
            # A changing state is uncertain until the confirmation window ends.
            self.current = self.last_confirmed if state == self.last_confirmed and not self.interrupted else "unknown"
            if self.current == "unknown":
                if self.uncertain_since is None:
                    self.uncertain_since = now
                if now - self.uncertain_since >= self.unavailable_seconds and not self.unavailable_reported:
                    self.unavailable_reported = True
                    return Event("unavailable", "unknown")
            return None
        self.uncertain_since = None
        old = self.last_confirmed
        self.last_confirmed = self.current = state
        if self.unavailable_reported:
            self.unavailable_reported = False
            self.interrupted = False
            return Event("restored", state)
        if old is None:
            self.interrupted = False
            return Event("initial", state)
        if old != state:
            kind = "current" if self.interrupted else ("freed" if state == "free" else "occupied")
            self.interrupted = False
            return Event(kind, state)
        self.interrupted = False
        return None
