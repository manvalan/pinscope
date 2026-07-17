"""Open-core billing seam.

Everything outside the billing modules (``credits``, ``credit_grants``,
``stripe_billing``, ``stripe_customer_map``, ``auto_topup`` and the
``billing``/``credits`` routers) talks to billing exclusively through
:func:`get_billing`. With ``BILLING_ENABLED=false`` the returned
:class:`NullBilling` makes every pipeline run free — the same shape as the
existing admin ``free=True`` path — so the core can run with no credits
ledger, no Stripe, and no billing routes mounted.

This module must stay a leaf: no billing module is imported at module
level (``CreditsBilling`` lazy-imports inside each method), so the core
never touches the Stripe SDK when billing is disabled.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from backend.config import settings

if TYPE_CHECKING:
    from backend.services.storage import StorageBackend


class InsufficientCredits(RuntimeError):
    """Raised when a charge would drop the balance below zero."""

    def __init__(self, required: float, available: float) -> None:
        super().__init__(
            f"Insufficient credits: required {required}, available {available}"
        )
        self.required = required
        self.available = available


class BillingHook(Protocol):
    """The full billing surface the core is allowed to depend on."""

    def credits_for_api_cost(self, cost_usd: float) -> float: ...

    def get_balance(self, storage: "StorageBackend", user_id: str) -> float: ...

    def charge(
        self,
        storage: "StorageBackend",
        user_id: str,
        amount: float,
        *,
        reason: str = "pipeline_charge",
        run_id: str | None = None,
        unit_id: str | None = None,
        allow_overdraft: bool = False,
    ) -> None: ...

    def ensure_trial_grant(self, storage: "StorageBackend", user_id: str) -> bool: ...

    def list_user_ids(self, storage: "StorageBackend") -> list[str]: ...

    async def maybe_auto_topup(
        self, storage: "StorageBackend", user_id: str
    ) -> dict | None: ...


class NullBilling:
    """Billing disabled: everything is free and nothing is written.

    ``credits_for_api_cost`` returning 0.0 is the linchpin — every
    ``ApiLogger`` entry gets ``credits_charged=0``, so the pipeline's
    charge path early-returns and the credit gate always allows.
    """

    def credits_for_api_cost(self, cost_usd: float) -> float:
        return 0.0

    def get_balance(self, storage: "StorageBackend", user_id: str) -> float:
        return 0.0

    def charge(
        self,
        storage: "StorageBackend",
        user_id: str,
        amount: float,
        *,
        reason: str = "pipeline_charge",
        run_id: str | None = None,
        unit_id: str | None = None,
        allow_overdraft: bool = False,
    ) -> None:
        return None

    def ensure_trial_grant(self, storage: "StorageBackend", user_id: str) -> bool:
        return False

    def list_user_ids(self, storage: "StorageBackend") -> list[str]:
        return []

    async def maybe_auto_topup(
        self, storage: "StorageBackend", user_id: str
    ) -> dict | None:
        return None


class CreditsBilling:
    """Production billing: delegates to the credits ledger + auto top-up."""

    def credits_for_api_cost(self, cost_usd: float) -> float:
        from backend.services import credits as credits_svc

        return credits_svc.credits_for_api_cost(cost_usd)

    def get_balance(self, storage: "StorageBackend", user_id: str) -> float:
        from backend.services import credits as credits_svc

        return credits_svc.get_balance(storage, user_id)

    def charge(
        self,
        storage: "StorageBackend",
        user_id: str,
        amount: float,
        *,
        reason: str = "pipeline_charge",
        run_id: str | None = None,
        unit_id: str | None = None,
        allow_overdraft: bool = False,
    ) -> None:
        from backend.services import credits as credits_svc

        credits_svc.charge(
            storage, user_id, amount,
            reason=reason,
            run_id=run_id,
            unit_id=unit_id,
            allow_overdraft=allow_overdraft,
        )

    def ensure_trial_grant(self, storage: "StorageBackend", user_id: str) -> bool:
        from backend.services import credits as credits_svc

        return credits_svc.ensure_trial_grant(storage, user_id)

    def list_user_ids(self, storage: "StorageBackend") -> list[str]:
        from backend.services import credits as credits_svc

        return credits_svc.list_user_ids(storage)

    async def maybe_auto_topup(
        self, storage: "StorageBackend", user_id: str
    ) -> dict | None:
        """Run an auto top-up attempt if configured.

        Returns ``{"reason", "amount_usd"}`` when this call produced a NEW
        failed attempt (so the caller can notify the user), else None.
        """
        from backend.services.auto_topup import get_config, maybe_trigger

        before = get_config(storage, user_id).last_attempt_ts
        try:
            await maybe_trigger(storage, user_id)
        except Exception:
            return None
        after = get_config(storage, user_id)
        if (
            after.last_attempt_status == "failed"
            and after.last_attempt_ts
            and after.last_attempt_ts != before
        ):
            return {
                "reason": after.last_failure_reason or "unknown",
                "amount_usd": after.amount_usd,
            }
        return None


_NULL = NullBilling()
_credits_billing: CreditsBilling | None = None


def get_billing() -> BillingHook:
    """Return the active billing implementation.

    Selected per call (not at import) so the ``billing_enabled`` setting
    can be monkeypatched in tests and so importing this module never pulls
    in billing code.
    """
    if not settings.billing_enabled:
        return _NULL
    global _credits_billing
    if _credits_billing is None:
        _credits_billing = CreditsBilling()
    return _credits_billing
