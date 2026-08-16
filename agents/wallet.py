"""
The project's crypto wallet — Nora's (the Steward's) domain.

Owner ask, 2026-08-14: a cross-chain wallet that can receive giving, hold a
treasury for the PeaceAntz DAO idea, show holdings in the Steward's report, and
pay real expenses — with Nora able to send within a hard cap. Sheraj chose the
most capable option at every step after the irreversibility risk was put to him
plainly, so this module's job is to make that as safe as it can honestly be.

The shape of the safety, all of it in CODE rather than in any prompt:

  * **Two wallets, not one.** `hot` is agent-spendable and holds a small float;
    `treasury` is a list of WATCH-ONLY addresses Sheraj controls elsewhere. Nora
    can read the treasury and can never touch it. That gives the DAO treasury a
    home without putting it inside an LLM's blast radius.
  * **Nora may only ever send USDC.** A stablecoin makes a dollar cap mean what
    it says, with no price feed to go stale or be manipulated. Native-token
    sends exist for gas but are OWNER-ONLY, never exposed as a tool.
  * **Destinations are allowlisted, and the allowlist is owner-only.** Exactly
    rule 28's discipline: no tool writes it. A fully prompt-injected Nora can
    still only move money to addresses Sheraj entered himself.
  * **Caps are enforced here**, per-transaction and per-day, and computed from
    the on-chain ledger rather than from anything the model says.
  * **Mainnet is opt-in.** Without WALLET_ALLOW_MAINNET=true only testnets are
    usable, so the whole thing can be exercised for real with no money at risk.
  * **Token contracts are verified on-chain** (symbol + decimals) before any
    transfer. The addresses below were checked live on 2026-08-14, but a
    hardcoded address is exactly the kind of thing that must not be trusted
    from memory — a wrong one silently destroys funds.

Signing needs `eth-account` (secp256k1/keccak/RLP — never hand-rolled). It is a
HARD dependency of sending, imported lazily so that balances, history and the
receive address all keep working without it, and `sending_available()` reports
the truth rather than failing at the worst moment.
"""

import json
import os
import sqlite3
import time
from decimal import Decimal
from pathlib import Path

import requests
from dotenv import load_dotenv

# Loaded HERE, not left to whoever imports first. Every limit below is read at
# module-import time, and agents.state (this module's only other import) does
# not load .env — so importing wallet before router silently gave every cap its
# built-in default. A tightened WALLET_MAX_PER_TX_USDC quietly not applying is
# a safety bug, not a config annoyance.
load_dotenv(dotenv_path=str(Path(__file__).parent.parent / ".env"))

from agents.state import DB_PATH  # noqa: E402

PRIVATE_DIR = Path(__file__).parent.parent / "private" / "wallet"
KEYSTORE_PATH = PRIVATE_DIR / "hot-keystore.json"

# Opt-in: without this, only testnet chains are selectable and nothing of value
# can move. Deliberately default-off so the whole feature can be tried safely.
ALLOW_MAINNET = os.getenv("WALLET_ALLOW_MAINNET", "false").strip().lower() == "true"

# Caps, in USDC (= dollars). Enforced in code; the model cannot argue with them.
MAX_PER_TX_USDC = Decimal(os.getenv("WALLET_MAX_PER_TX_USDC", "25"))
DAILY_CAP_USDC = Decimal(os.getenv("WALLET_DAILY_CAP_USDC", "50"))
# At or below this, Nora sends directly. Above it (up to MAX_PER_TX) the send
# queues for Sheraj's approval. Above MAX_PER_TX it is refused outright.
AUTO_SEND_USDC = Decimal(os.getenv("WALLET_AUTO_SEND_USDC", "5"))

USDC_DECIMALS = 6
_ERC20_TRANSFER = "0xa9059cbb"
_ERC20_BALANCE_OF = "0x70a08231"
_ERC20_SYMBOL = "0x95d89b41"
_ERC20_DECIMALS = "0x313ce567"


class WalletError(RuntimeError):
    """Anything that should stop a transfer and be shown to Sheraj verbatim."""


# ── Chains ────────────────────────────────────────────────────────────────────
# One EVM keypair works on every chain here — that is what "cross-chain" means
# in practice: one address, many networks. Non-EVM chains (Solana, Bitcoin) use
# different key derivation entirely and are NOT covered; claiming otherwise
# would be a lie about where funds can land.
#
# usdc addresses verified live against each RPC on 2026-08-14 (symbol == USDC,
# decimals == 6) and re-verified at runtime by verify_token().

CHAINS: dict[str, dict] = {
    "base": {
        "name": "Base", "chain_id": 8453, "testnet": False,
        "rpc": os.getenv("RPC_BASE", "https://mainnet.base.org"),
        "explorer": "https://basescan.org",
        "native": "ETH",
        "usdc": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    },
    "arbitrum": {
        "name": "Arbitrum One", "chain_id": 42161, "testnet": False,
        "rpc": os.getenv("RPC_ARBITRUM", "https://arb1.arbitrum.io/rpc"),
        "explorer": "https://arbiscan.io",
        "native": "ETH",
        "usdc": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
    },
    "optimism": {
        "name": "OP Mainnet", "chain_id": 10, "testnet": False,
        "rpc": os.getenv("RPC_OPTIMISM", "https://mainnet.optimism.io"),
        "explorer": "https://optimistic.etherscan.io",
        "native": "ETH",
        "usdc": "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85",
    },
    "base-sepolia": {
        "name": "Base Sepolia (test)", "chain_id": 84532, "testnet": True,
        "rpc": os.getenv("RPC_BASE_SEPOLIA", "https://sepolia.base.org"),
        "explorer": "https://sepolia.basescan.org",
        "native": "ETH",
        "usdc": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
    },
    # Added 2026-08-14: "Sepolia" and "Base Sepolia" are DIFFERENT networks, and
    # most generic faucets hand out the former. Sheraj funded the wallet on this
    # one and the dashboard showed nothing, because it was only ever reading
    # Base Sepolia. usdc verified on-chain the same day (name/symbol USDC,
    # decimals 6).
    "sepolia": {
        "name": "Ethereum Sepolia (test)", "chain_id": 11155111, "testnet": True,
        "rpc": os.getenv("RPC_SEPOLIA", "https://ethereum-sepolia-rpc.publicnode.com"),
        "explorer": "https://sepolia.etherscan.io",
        "native": "ETH",
        "usdc": "0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238",
    },
}

DEFAULT_CHAIN = os.getenv("WALLET_DEFAULT_CHAIN", "base-sepolia")


def enabled_chains() -> dict[str, dict]:
    """Chains usable right now. Mainnets appear only when explicitly enabled."""
    return {k: v for k, v in CHAINS.items() if v["testnet"] or ALLOW_MAINNET}


def get_chain(chain: str) -> dict:
    cfg = enabled_chains().get(chain)
    if not cfg:
        if chain in CHAINS:
            raise WalletError(
                f"{CHAINS[chain]['name']} is a live network and is switched off. "
                "Set WALLET_ALLOW_MAINNET=true in .env to enable real funds.")
        raise WalletError(f"Unknown chain '{chain}' — known: {', '.join(enabled_chains())}")
    return cfg


# ── Raw JSON-RPC (no web3 dependency for reads) ───────────────────────────────

def _rpc(chain: str, method: str, params: list):
    cfg = get_chain(chain)
    try:
        resp = requests.post(cfg["rpc"],
                             json={"jsonrpc": "2.0", "method": method,
                                   "params": params, "id": 1},
                             timeout=20)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as e:
        raise WalletError(f"Could not reach {cfg['name']}: {type(e).__name__}: {e}")
    if "error" in payload:
        raise WalletError(f"{cfg['name']} rejected the request: {payload['error']}")
    return payload.get("result")


def _pad_address(address: str) -> str:
    return address.lower().replace("0x", "").rjust(64, "0")


def _decode_string(hexstr: str | None) -> str:
    if not hexstr or hexstr == "0x":
        return ""
    raw = bytes.fromhex(hexstr[2:])
    if len(raw) < 64:
        return raw.rstrip(b"\x00").decode("utf-8", "replace")
    length = int.from_bytes(raw[32:64], "big")
    return raw[64:64 + length].decode("utf-8", "replace")


def is_address(value: str) -> bool:
    """A syntactically valid EVM address. Not a claim that it exists."""
    v = (value or "").strip()
    return len(v) == 42 and v.startswith("0x") and all(
        c in "0123456789abcdefABCDEF" for c in v[2:])


def verify_token(chain: str) -> dict:
    """
    Confirm the configured USDC contract really is USDC, on-chain.

    A hardcoded token address is precisely the thing that must never be trusted
    from memory: a wrong one means a transfer call to something that is not the
    token, and the funds are gone with no error. Called before every send.
    """
    cfg = get_chain(chain)
    addr = cfg["usdc"]
    symbol = _decode_string(_rpc(chain, "eth_call", [{"to": addr, "data": _ERC20_SYMBOL}, "latest"]))
    dec_raw = _rpc(chain, "eth_call", [{"to": addr, "data": _ERC20_DECIMALS}, "latest"])
    decimals = int(dec_raw, 16) if dec_raw and dec_raw != "0x" else None
    ok = symbol.upper() == "USDC" and decimals == USDC_DECIMALS
    if not ok:
        raise WalletError(
            f"The USDC address configured for {cfg['name']} does not look like USDC "
            f"(symbol={symbol!r}, decimals={decimals}). Refusing to send — this is "
            "exactly how funds get destroyed. Check wallet.CHAINS.")
    return {"chain": chain, "address": addr, "symbol": symbol, "decimals": decimals}


# ── The hot wallet's key ──────────────────────────────────────────────────────

def _passphrase() -> str:
    phrase = os.getenv("WALLET_PASSPHRASE", "")
    if not phrase:
        raise WalletError(
            "WALLET_PASSPHRASE is not set in .env. The wallet key is stored "
            "encrypted with it, and without it the key can neither be created "
            "nor read.")
    return phrase


def sending_available() -> tuple[bool, str]:
    """
    (can_sign, reason). Signing needs eth-account; reads never do.

    Reported honestly up front rather than discovered at the moment of a
    transfer — the same discipline as the chained-video PyAV preflight.
    """
    try:
        import eth_account  # noqa: F401
    except ImportError:
        return False, ("The signing library isn't installed yet. Run:  "
                       "pip install eth-account")
    if not KEYSTORE_PATH.exists():
        return False, "No wallet has been created yet."
    if not os.getenv("WALLET_PASSPHRASE", ""):
        return False, "WALLET_PASSPHRASE is not set in .env."
    return True, ""


def wallet_exists() -> bool:
    return KEYSTORE_PATH.exists()


def create_wallet(overwrite: bool = False) -> dict:
    """
    Generate the hot wallet and store it as an encrypted keystore in private/.

    Returns the ADDRESS and, once only, the private key so Sheraj can back it up
    somewhere safe. The key is never logged, never returned again, and never
    leaves this machine.
    """
    try:
        from eth_account import Account
    except ImportError:
        raise WalletError("Install the signing library first:  pip install eth-account")

    if KEYSTORE_PATH.exists() and not overwrite:
        raise WalletError(
            "A wallet already exists. Refusing to overwrite it — if the old key is "
            "lost, so is anything still held at that address. Move "
            f"{KEYSTORE_PATH} aside by hand if you really mean to replace it.")

    PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
    account = Account.create()
    keystore = Account.encrypt(account.key, _passphrase())
    KEYSTORE_PATH.write_text(json.dumps(keystore), encoding="utf-8")
    set_setting("hot_address", account.address)
    return {
        "address": account.address,
        # Shown ONCE, in the dashboard, for offline backup.
        "private_key": account.key.hex(),
        "warning": ("Write this key down somewhere offline and keep it. It is shown "
                    "only now. Anyone who has it can take everything in this wallet."),
    }


def hot_address() -> str | None:
    cached = get_setting("hot_address", "")
    if cached:
        return cached
    if not KEYSTORE_PATH.exists():
        return None
    try:
        data = json.loads(KEYSTORE_PATH.read_text(encoding="utf-8"))
        addr = data.get("address", "")
        return ("0x" + addr) if addr and not addr.startswith("0x") else (addr or None)
    except Exception:
        return None


def _load_key():
    from eth_account import Account
    keystore = json.loads(KEYSTORE_PATH.read_text(encoding="utf-8"))
    return Account.from_key(Account.decrypt(keystore, _passphrase()))


# ── Balances ──────────────────────────────────────────────────────────────────

def _native_balance(chain: str, address: str) -> Decimal:
    wei = int(_rpc(chain, "eth_getBalance", [address, "latest"]) or "0x0", 16)
    return Decimal(wei) / Decimal(10 ** 18)


def _usdc_balance(chain: str, address: str) -> Decimal:
    cfg = get_chain(chain)
    data = _ERC20_BALANCE_OF + _pad_address(address)
    raw = _rpc(chain, "eth_call", [{"to": cfg["usdc"], "data": data}, "latest"])
    units = int(raw or "0x0", 16)
    return Decimal(units) / Decimal(10 ** USDC_DECIMALS)


def balances(address: str | None = None) -> dict:
    """
    Holdings across every enabled chain for one address (default: the hot
    wallet). A chain that cannot be reached is REPORTED as unreachable rather
    than silently counted as zero — a zero that means "the RPC was down" would
    make the Steward's report quietly wrong.
    """
    addr = address or hot_address()
    if not addr:
        return {"address": None, "chains": [], "total_usdc": "0", "error": "No wallet yet."}
    # Real and play money are totalled SEPARATELY. A combined figure had the
    # Steward reporting "$15" for a wallet holding nothing but testnet tokens,
    # which is exactly the kind of false number she exists to not produce.
    rows, total, test_total = [], Decimal(0), Decimal(0)
    for chain in enabled_chains():
        try:
            usdc = _usdc_balance(chain, addr)
            native = _native_balance(chain, addr)
            if CHAINS[chain]["testnet"]:
                test_total += usdc
            else:
                total += usdc
            rows.append({
                "chain": chain, "name": CHAINS[chain]["name"],
                "testnet": CHAINS[chain]["testnet"],
                "usdc": f"{usdc:.6f}", "native": f"{native:.6f}",
                "native_symbol": CHAINS[chain]["native"],
                "explorer": CHAINS[chain]["explorer"], "reachable": True,
            })
        except WalletError as e:
            rows.append({
                "chain": chain, "name": CHAINS[chain]["name"],
                "testnet": CHAINS[chain]["testnet"],
                "usdc": None, "native": None,
                "native_symbol": CHAINS[chain]["native"],
                "explorer": CHAINS[chain]["explorer"],
                "reachable": False, "error": str(e)[:200],
            })
    return {"address": addr, "chains": rows,
            "total_usdc": f"{total:.2f}",              # real money only
            "total_testnet_usdc": f"{test_total:.2f}"}  # play money, never mixed in


# ── Storage: ledger, owner-only allowlist, treasury, settings ─────────────────

def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_wallet_db():
    with _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS wallet_txs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chain TEXT NOT NULL,
                token TEXT NOT NULL,
                to_address TEXT NOT NULL,
                to_label TEXT DEFAULT '',
                amount TEXT NOT NULL,
                tx_hash TEXT,
                status TEXT DEFAULT 'sent',
                initiated_by TEXT NOT NULL,
                note TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS wallet_allowlist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT NOT NULL,
                address TEXT NOT NULL,
                note TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS wallet_treasury (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT NOT NULL,
                address TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS wallet_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_wallet_allowlist_address
                ON wallet_allowlist (address);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_wallet_treasury_address
                ON wallet_treasury (address);
        """)
        conn.commit()


def get_setting(key: str, default: str = "") -> str:
    try:
        with _connect() as conn:
            row = conn.execute("SELECT value FROM wallet_settings WHERE key = ?",
                               (key,)).fetchone()
        return row["value"] if row else default
    except Exception:
        return default


def set_setting(key: str, value: str):
    with _connect() as conn:
        conn.execute(
            "INSERT INTO wallet_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
        conn.commit()


# The allowlist is OWNER-ONLY. No tool in colony_tools writes it, exactly as the
# WhatsApp contacts allowlist is owner-only (rule 28) — it is the reason a
# prompt-injected Nora still cannot send money to an attacker.
def add_allowlist(label: str, address: str, note: str = "") -> dict:
    if not is_address(address):
        raise WalletError(f"'{address}' is not a valid wallet address.")
    if not label.strip():
        raise WalletError("Give the address a name you'll recognise later.")
    with _connect() as conn:
        conn.execute(
            "INSERT INTO wallet_allowlist (label, address, note) VALUES (?, ?, ?) "
            "ON CONFLICT(address) DO UPDATE SET label = excluded.label, note = excluded.note",
            (label.strip(), address.strip(), note.strip()))
        conn.commit()
    return {"label": label.strip(), "address": address.strip()}


def list_allowlist() -> list[dict]:
    with _connect() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM wallet_allowlist ORDER BY label").fetchall()]


def remove_allowlist(entry_id: int):
    with _connect() as conn:
        conn.execute("DELETE FROM wallet_allowlist WHERE id = ?", (entry_id,))
        conn.commit()


def allowlisted(address: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM wallet_allowlist WHERE lower(address) = ?",
                           ((address or "").strip().lower(),)).fetchone()
    return dict(row) if row else None


def add_treasury(label: str, address: str) -> dict:
    """A watch-only address Sheraj controls elsewhere. Nora can never spend it."""
    if not is_address(address):
        raise WalletError(f"'{address}' is not a valid wallet address.")
    with _connect() as conn:
        conn.execute(
            "INSERT INTO wallet_treasury (label, address) VALUES (?, ?) "
            "ON CONFLICT(address) DO UPDATE SET label = excluded.label",
            (label.strip() or "Treasury", address.strip()))
        conn.commit()
    return {"label": label, "address": address}


def list_treasury() -> list[dict]:
    with _connect() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM wallet_treasury ORDER BY label").fetchall()]


def remove_treasury(entry_id: int):
    with _connect() as conn:
        conn.execute("DELETE FROM wallet_treasury WHERE id = ?", (entry_id,))
        conn.commit()


def record_tx(chain: str, token: str, to_address: str, amount: str, tx_hash: str | None,
              initiated_by: str, to_label: str = "", note: str = "",
              status: str = "sent") -> int:
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO wallet_txs (chain, token, to_address, to_label, amount,
               tx_hash, status, initiated_by, note)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (chain, token, to_address, to_label, amount, tx_hash, status,
             initiated_by, note))
        conn.commit()
        return cur.lastrowid


def history(limit: int = 50) -> list[dict]:
    with _connect() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM wallet_txs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]
    for r in rows:
        cfg = CHAINS.get(r["chain"], {})
        r["explorer_url"] = (f"{cfg['explorer']}/tx/{r['tx_hash']}"
                             if cfg.get("explorer") and r.get("tx_hash") else None)
    return rows


def spent_today_usdc() -> Decimal:
    """
    Real money that has actually left the wallet today, from the ledger — never
    from anything the model reports about itself.

    TESTNET sends are excluded. Play money is not money, and counting it would
    let a few practice transfers silently consume the real daily budget (caught
    for real on the day mainnet was switched on: a $5 Base Sepolia test had
    already eaten $5 of the $50 cap).
    """
    live = {c for c, cfg in CHAINS.items() if not cfg["testnet"]}
    if not live:
        return Decimal(0)
    placeholders = ",".join("?" for _ in live)
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT amount FROM wallet_txs WHERE token = 'USDC' AND status != 'failed' "
            f"AND date(created_at) = date('now') AND chain IN ({placeholders})",
            tuple(live)).fetchall()
    total = Decimal(0)
    for r in rows:
        try:
            total += Decimal(r["amount"])
        except Exception:
            continue
    return total


# ── The cap check ─────────────────────────────────────────────────────────────

def check_limits(amount_usdc: Decimal, to_address: str) -> dict:
    """
    Decide what may happen to a proposed send, deterministically.

    Returns {"decision": "auto" | "approval" | "refused", "reason": str}.
    Called by BOTH Nora's tool and the dashboard's own send form, so the caps
    hold no matter who initiates.
    """
    entry = allowlisted(to_address)
    if not entry:
        return {"decision": "refused", "reason": (
            f"{to_address} is not on the approved-addresses list. Sheraj adds addresses "
            "himself in the Treasury tab; Nora cannot add them.")}
    if amount_usdc <= 0:
        return {"decision": "refused", "reason": "Amount must be more than zero."}
    if amount_usdc > MAX_PER_TX_USDC:
        return {"decision": "refused", "reason": (
            f"${amount_usdc} is over the ${MAX_PER_TX_USDC} single-payment limit.")}
    already = spent_today_usdc()
    if already + amount_usdc > DAILY_CAP_USDC:
        return {"decision": "refused", "reason": (
            f"${amount_usdc} would take today's total to ${already + amount_usdc}, "
            f"over the ${DAILY_CAP_USDC} daily limit (${already} already sent today).")}
    if amount_usdc > AUTO_SEND_USDC:
        return {"decision": "approval", "reason": (
            f"${amount_usdc} is over the ${AUTO_SEND_USDC} auto-send limit, so it "
            "needs your approval first.")}
    return {"decision": "auto", "reason": ""}


# ── Sending ───────────────────────────────────────────────────────────────────

def _fee_fields(chain: str) -> dict:
    """EIP-1559 fees, with a floor so a zero-tip testnet reading still lands."""
    block = _rpc(chain, "eth_getBlockByNumber", ["latest", False]) or {}
    base = int(block.get("baseFeePerGas") or "0x0", 16)
    try:
        tip = int(_rpc(chain, "eth_maxPriorityFeePerGas", []) or "0x0", 16)
    except WalletError:
        tip = 0
    tip = max(tip, 1_000_000)                 # 0.001 gwei floor
    return {"maxPriorityFeePerGas": tip, "maxFeePerGas": base * 2 + tip}


def send_usdc(chain: str, to_address: str, amount: str | Decimal,
              initiated_by: str, note: str = "", bypass_limits: bool = False) -> dict:
    """
    Move USDC. The ONLY spending path exposed to Nora.

    `bypass_limits` is for a send Sheraj has already approved through the queue
    — the caps were checked when it was queued, and re-checking would refuse a
    payment he explicitly authorised. It never skips the ALLOWLIST or the
    token-contract verification, which hold unconditionally.
    """
    amount = Decimal(str(amount))
    cfg = get_chain(chain)

    # The allowlist is checked FIRST, before anything else can fail. It is the
    # control that survives a prompt injection, so it must be the one that
    # cannot be pre-empted by an unrelated error (a missing signing library
    # once masked it, which made the refusal look like a setup problem rather
    # than a security decision).
    entry = allowlisted(to_address)
    if not entry:
        raise WalletError(
            f"{to_address} is not on the approved-addresses list — refusing to send.")

    ok, why = sending_available()
    if not ok:
        raise WalletError(why)

    if not bypass_limits:
        verdict = check_limits(amount, to_address)
        if verdict["decision"] != "auto":
            raise WalletError(verdict["reason"])

    # Never trust the hardcoded token address without checking it is the token.
    verify_token(chain)

    account = _load_key()
    units = int(amount * (10 ** USDC_DECIMALS))
    data = (_ERC20_TRANSFER + _pad_address(to_address)
            + format(units, "x").rjust(64, "0"))
    tx = {
        "chainId": cfg["chain_id"],
        "to": cfg["usdc"],
        "value": 0,
        "data": "0x" + data if not data.startswith("0x") else data,
        "nonce": int(_rpc(chain, "eth_getTransactionCount",
                          [account.address, "pending"]) or "0x0", 16),
        **_fee_fields(chain),
    }
    try:
        gas = int(_rpc(chain, "eth_estimateGas",
                       [{"from": account.address, "to": tx["to"], "data": tx["data"]}]), 16)
    except WalletError as e:
        raise WalletError(
            f"The network refused to price this transfer, which usually means the "
            f"wallet has no {cfg['native']} for gas on {cfg['name']}, or not enough "
            f"USDC. ({e})")
    tx["gas"] = int(gas * 1.25)

    signed = account.sign_transaction(tx)
    raw = signed.raw_transaction if hasattr(signed, "raw_transaction") else signed.rawTransaction
    tx_hash = _rpc(chain, "eth_sendRawTransaction", ["0x" + raw.hex().replace("0x", "")])

    tx_id = record_tx(chain, "USDC", to_address, f"{amount}", tx_hash,
                      initiated_by, to_label=entry["label"], note=note)
    return {
        "id": tx_id, "tx_hash": tx_hash, "chain": chain, "amount": f"{amount}",
        "to": to_address, "to_label": entry["label"],
        "explorer_url": f"{cfg['explorer']}/tx/{tx_hash}",
    }


def wait_for_receipt(chain: str, tx_hash: str, timeout_s: int = 90) -> dict | None:
    """Poll for a receipt. Returns None on timeout — pending is not failed."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        receipt = _rpc(chain, "eth_getTransactionReceipt", [tx_hash])
        if receipt:
            return receipt
        time.sleep(3)
    return None


def status() -> dict:
    """Everything the dashboard needs to describe the wallet honestly."""
    init_wallet_db()
    can_sign, why = sending_available()
    return {
        "exists": wallet_exists(),
        "address": hot_address(),
        "can_send": can_sign,
        "cannot_send_reason": why,
        "mainnet_enabled": ALLOW_MAINNET,
        "default_chain": DEFAULT_CHAIN,
        "chains": [
            {"id": k, "name": v["name"], "testnet": v["testnet"],
             "explorer": v["explorer"], "native": v["native"]}
            for k, v in enabled_chains().items()
        ],
        "limits": {
            "auto_send_usdc": str(AUTO_SEND_USDC),
            "max_per_tx_usdc": str(MAX_PER_TX_USDC),
            "daily_cap_usdc": str(DAILY_CAP_USDC),
            "spent_today_usdc": str(spent_today_usdc()),
        },
        "allowlist": list_allowlist(),
        "treasury": list_treasury(),
    }
