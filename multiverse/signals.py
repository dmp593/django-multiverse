from django.dispatch import Signal

#: Sent whenever the tenant active on the current thread changes.
#:
#: :param sender: the tenant model class, so receivers can filter on it
#: :param instance: the tenant now active, or ``None`` when one was released
#:
#: The signal is symmetric: it fires on activation *and* on deactivation.
#: Receivers that namespace a cache, set a logging context or swap a storage
#: backend need the release notification just as much as the activation, and
#: earlier releases only ever sent the first half.
tenant_changed = Signal()
