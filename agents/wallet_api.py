"""
The project wallet's HTTP routes (rules 42-49), extracted from api.py.

No prefix on this router (and every other one this session added): most of
the moved-out sections mix several path families (e.g. colony_api.py serves
both /colony/* and /nuclei/*), so every route keeps its exact original path
string rather than relying on prefix concatenation -- the one thing this
extraction must never do is change a URL (rule: mechanical extraction only).

Business logic lives entirely in agents/wallet.py; every route here is a
thin, lazily-imported wrapper, unchanged from its original body in api.py.
"""

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()


class AllowlistRequest(BaseModel):
    label: str
    address: str
    note: str = ""


class TreasuryRequest(BaseModel):
    label: str
    address: str


class WalletSendRequest(BaseModel):
    to: str
    amount: str
    chain: Optional[str] = None
    note: str = ""


@router.get("/wallet/status")
def wallet_status():
    from agents import wallet
    return wallet.status()


@router.get("/wallet/balances")
def wallet_balances(address: Optional[str] = None):
    """Live holdings. An unreachable chain reports as unreachable, never as zero."""
    from agents import wallet
    try:
        out = wallet.balances(address)
    except wallet.WalletError as e:
        raise HTTPException(status_code=502, detail=str(e))
    out["treasury"] = [
        t | {"balances": _safe_balances(t["address"])} for t in wallet.list_treasury()
    ]
    return out


def _safe_balances(address: str) -> dict | None:
    from agents import wallet
    try:
        return wallet.balances(address)
    except Exception:
        return None


@router.post("/wallet/create")
def wallet_create():
    """
    Create the hot wallet. Returns the private key ONCE so Sheraj can back it
    up offline; it is never returned again and never logged.
    """
    from agents import wallet
    try:
        return wallet.create_wallet()
    except wallet.WalletError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/wallet/history")
def wallet_history(limit: int = 50):
    from agents import wallet
    wallet.init_wallet_db()
    return {"transactions": wallet.history(limit)}


@router.post("/wallet/allowlist")
def wallet_add_allowlist(req: AllowlistRequest):
    """
    Approve an address to receive funds. OWNER-ONLY by construction: no tool in
    colony_tools writes this table, mirroring the WhatsApp contacts allowlist
    (rule 28). It is what stops a prompt-injected Nora paying an attacker.
    """
    from agents import wallet
    wallet.init_wallet_db()
    try:
        return wallet.add_allowlist(req.label, req.address, req.note)
    except wallet.WalletError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.delete("/wallet/allowlist/{entry_id}")
def wallet_remove_allowlist(entry_id: int):
    from agents import wallet
    wallet.remove_allowlist(entry_id)
    return {"result": "ok"}


@router.post("/wallet/treasury")
def wallet_add_treasury(req: TreasuryRequest):
    """A watch-only address held elsewhere. Nora can read it and never spend it."""
    from agents import wallet
    wallet.init_wallet_db()
    try:
        return wallet.add_treasury(req.label, req.address)
    except wallet.WalletError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.delete("/wallet/treasury/{entry_id}")
def wallet_remove_treasury(entry_id: int):
    from agents import wallet
    wallet.remove_treasury(entry_id)
    return {"result": "ok"}


@router.post("/wallet/send")
def wallet_send(req: WalletSendRequest):
    """
    Sheraj's OWN send, straight from the dashboard with no LLM anywhere in the
    path — the safest way to move money here.

    It still requires the destination to be on the allowlist, because that also
    catches a mistyped address, and a wrong address is unrecoverable. It does
    NOT apply Nora's caps: those exist to bound an agent, not its owner.
    """
    from agents import wallet
    wallet.init_wallet_db()
    try:
        return wallet.send_usdc(
            req.chain or wallet.DEFAULT_CHAIN, req.to, req.amount,
            initiated_by="sheraj", note=req.note, bypass_limits=True)
    except wallet.WalletError as e:
        raise HTTPException(status_code=400, detail=str(e))
