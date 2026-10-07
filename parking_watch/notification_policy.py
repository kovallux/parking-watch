from .state import Event


class NotificationPolicy:
    """Keep parking changes prompt and noisy recognition recoveries quiet."""

    def __init__(self, offline_seconds=15, uncertain_seconds=300,
                 healthy_seconds=60, repeat_seconds=1800):
        self.offline_seconds = offline_seconds
        self.uncertain_seconds = uncertain_seconds
        self.healthy_seconds = healthy_seconds
        self.repeat_seconds = repeat_seconds
        self.confirmed = None
        self.unstable_since = None
        self.healthy_since = None
        self.healthy_state = None
        self.offline_since = None
        self.camera_warning_latched = False
        self.camera_warning_active = False
        self.uncertain_warning_latched = False
        self.last_uncertain_warning = None

    def observe(self, state, observation, event, now):
        if observation == "offline":
            if self.offline_since is None:
                self.offline_since = now
        else:
            self.offline_since = None

        if state in {"free", "occupied"}:
            previous = self.confirmed
            self.confirmed = state
            if previous is None:
                self.unstable_since = None
            if self.healthy_since is None or self.healthy_state != state:
                self.healthy_since, self.healthy_state = now, state
            if now - self.healthy_since >= self.healthy_seconds:
                self.unstable_since = None
                self.uncertain_warning_latched = False
                self.camera_warning_latched = False
            recovered_camera = self.camera_warning_active
            self.camera_warning_active = False
            if previous is not None and previous != state:
                if event and event.kind in {"freed", "occupied"}:
                    return event
                return Event("current", state)
            if recovered_camera:
                return Event("restored", state)
            return None

        self.healthy_since, self.healthy_state = None, None
        if self.unstable_since is None:
            self.unstable_since = now
        if self.offline_since is not None:
            if now - self.offline_since >= self.offline_seconds and not self.camera_warning_latched:
                self.camera_warning_latched = self.camera_warning_active = True
                return Event("unavailable", "offline")
            return None
        elapsed = now - self.unstable_since
        repeat_allowed = (self.last_uncertain_warning is None
                          or now - self.last_uncertain_warning >= self.repeat_seconds)
        if elapsed >= self.uncertain_seconds and not self.uncertain_warning_latched and repeat_allowed:
            self.uncertain_warning_latched = True
            self.last_uncertain_warning = now
            return Event("uncertain", "unknown")
        return None
