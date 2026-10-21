"""TTL-based expiry logic for preview namespaces.

The cleanup CronJob lists namespaces labelled ``preview.env/platform=true``
and deletes the ones older than the TTL. The decision logic here is pure;
the kubectl I/O lives in :mod:`previewctl.kube`.
"""

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

PLATFORM_LABEL = "preview.env/platform"
PR_LABEL = "preview.env/pr"
LAST_DEPLOYED_ANNOTATION = "preview.env/last-deployed"

_TTL_PATTERN = re.compile(r"^(\d+)([smhd])$")
_TTL_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}


def parse_ttl(text: str) -> timedelta:
    """Parse a TTL like ``'30m'``, ``'24h'`` or ``'7d'`` into a timedelta."""
    match = _TTL_PATTERN.match(text.strip().lower())
    if not match:
        raise ValueError(f"invalid TTL {text!r}; use e.g. '30m', '24h', '7d'")
    amount, unit = match.groups()
    return timedelta(**{_TTL_UNITS[unit]: int(amount)})


@dataclass(frozen=True)
class PreviewEnvironment:
    namespace: str
    created_at: datetime
    pr: int | None = None
    last_deployed_at: datetime | None = None

    def is_expired(self, ttl: timedelta, now: datetime) -> bool:
        # The TTL measures age since the last deploy, not since the
        # namespace was first created, so a PR that is still being pushed
        # to does not lose its preview mid-review; fall back to creation
        # time for namespaces that predate this annotation.
        reference = self.last_deployed_at or self.created_at
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=timezone.utc)
        return now - reference >= ttl


def expired_environments(
    envs: list[PreviewEnvironment], ttl: timedelta, now: datetime
) -> list[PreviewEnvironment]:
    """Return the subset of environments that have outlived ``ttl``."""
    if ttl.total_seconds() <= 0:
        raise ValueError("ttl must be positive")
    return [env for env in envs if env.is_expired(ttl, now)]


def parse_namespace_list(raw: bytes | str) -> list[PreviewEnvironment]:
    """Parse the JSON output of ``kubectl get namespaces -o json``."""
    data = json.loads(raw)
    envs = []
    for item in data.get("items", []):
        meta = item.get("metadata", {})
        labels = meta.get("labels", {})
        annotations = meta.get("annotations", {}) or {}
        created = meta.get("creationTimestamp")
        if not created:
            continue
        pr = labels.get(PR_LABEL)
        last_deployed = annotations.get(LAST_DEPLOYED_ANNOTATION)
        envs.append(
            PreviewEnvironment(
                namespace=meta["name"],
                created_at=datetime.fromisoformat(created.replace("Z", "+00:00")),
                pr=int(pr) if pr and pr.isdigit() else None,
                last_deployed_at=(
                    datetime.fromisoformat(last_deployed.replace("Z", "+00:00"))
                    if last_deployed
                    else None
                ),
            )
        )
    return envs
