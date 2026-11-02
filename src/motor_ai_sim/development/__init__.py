"""Development project domain constants."""

PROJECT_STATUSES = ("spec", "design", "simulation", "quote", "prototype", "production", "closed")
#: The engineering stages a project member who is not MOTRES staff (in a self-service project the owner IS the customer)
#: may set.  ``quote`` / ``prototype`` / ``production`` are commercial and MOTRES staff's (global admin / engineer), and so
#: is every change of a project that already is in one of them or closed (owner 2026-11-02).
CUSTOMER_STATUSES = ("spec", "design", "simulation")


def status_change_allowed(staff: bool, current: str, new: str) -> bool:
    """May the caller move a project from ``current`` to ``new``?  Staff: any known status.  Anybody else: only between the
    engineering stages, plus closing a project that is still in one of them (the self-service limit answers "close one
    to open another"); never into, out of or within a commercial status, and never reopening a closed project."""
    if new not in PROJECT_STATUSES:
        return False
    if staff or new == current:
        return True
    if current not in CUSTOMER_STATUSES:
        return False
    return new in CUSTOMER_STATUSES or new == "closed"
MEMBER_ROLES = ("customer", "engineer", "owner")
