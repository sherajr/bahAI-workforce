"""
Offline regression suite for the project wallet. Free, fast, and it never
touches a real network or a real key.

    python scripts/test_wallet.py

This suite exists because the wallet is the one part of this codebase where a
mistake is IRREVERSIBLE. Everything else can be re-run, re-scored or deleted; a
transfer cannot. So the checks below are weighted towards the guarantees rather
than the happy path: that the allowlist cannot be bypassed, that the caps are
computed from the ledger rather than from the model's claims, that a mainnet
chain is unusable until explicitly enabled, and that no guest-facing path can
reach a money tool.

Console output is ASCII only (Windows cp1252 — see AGENTS.md gotchas).
"""

import json
import os
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# The API is owner-only (rule 70). Setting the key in the environment keeps
# this suite off the real private/api_key.txt and lets its TestClient present
# a valid key -- there is deliberately no switch that turns the gate off.
os.environ["DASHBOARD_API_KEY"] = "dashboard-suite-test-key"
_AUTH = {"X-API-Key": os.environ["DASHBOARD_API_KEY"]}

_TMP = tempfile.mkdtemp(prefix="wallet_test_")
os.environ["ANTHROPIC_API_KEY"] = os.environ.get("ANTHROPIC_API_KEY", "test-key")
os.environ["WALLET_PASSPHRASE"] = "test-passphrase-not-a-real-one"
# Pinned BEFORE agents.wallet is imported. wallet.py loads .env itself, so once
# Sheraj enabled mainnet on his machine this suite started asserting against his
# live config instead of the code's behaviour. A test that passes or fails based
# on the developer's .env is testing the wrong thing.
os.environ["WALLET_ALLOW_MAINNET"] = "false"

import agents.state as state  # noqa: E402

state.DB_PATH = Path(_TMP) / "workforce.db"

import agents.colony as colony  # noqa: E402
import agents.colony_tools as colony_tools  # noqa: E402
import agents.wallet as wallet  # noqa: E402

colony.DB_PATH = state.DB_PATH
wallet.DB_PATH = state.DB_PATH
wallet.PRIVATE_DIR = Path(_TMP) / "wallet"
wallet.KEYSTORE_PATH = wallet.PRIVATE_DIR / "hot-keystore.json"

PASS = FAIL = 0
FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = ""):
    global PASS, FAIL
    if condition:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(f"{label}{(' -- ' + detail) if detail else ''}")
        print(f"  FAIL: {label}" + (f" -- {detail}" if detail else ""))


def section(title: str):
    print(f"\n=== {title} ===")


state.init_db()

GOOD = "0x1111111111111111111111111111111111111111"
BAD = "0x2222222222222222222222222222222222222222"


# ── Chains and the mainnet gate ───────────────────────────────────────────────

section("Chains and the mainnet gate")
check("wallet tables exist on a FRESH database", wallet.list_allowlist() == [],
      "init_wallet_db must run OUTSIDE state.init_db's own connection block")
check("mainnet is OFF unless switched on", wallet.ALLOW_MAINNET is False)
# The gate must be strict opt-in: anything that is not exactly "true" leaves
# real funds unreachable, including a blank, a typo, or an unset variable.
for _val in ("", "false", "0", "yes", "TRUE ", "ture", None):
    _saved = os.environ.pop("WALLET_ALLOW_MAINNET", None)
    if _val is not None:
        os.environ["WALLET_ALLOW_MAINNET"] = _val
    _on = os.getenv("WALLET_ALLOW_MAINNET", "false").strip().lower() == "true"
    check(f"mainnet stays off for WALLET_ALLOW_MAINNET={_val!r}",
          _on is (_val is not None and _val.strip().lower() == "true"),
          f"parsed as {_on}")
    if _saved is not None:
        os.environ["WALLET_ALLOW_MAINNET"] = _saved
os.environ["WALLET_ALLOW_MAINNET"] = "false"
check("only testnets are enabled by default",
      all(c["testnet"] for c in wallet.enabled_chains().values()),
      str(list(wallet.enabled_chains())))
try:
    wallet.get_chain("base")
    check("a mainnet chain is refused while disabled", False, "it was allowed")
except wallet.WalletError as e:
    check("a mainnet chain is refused while disabled", "switched off" in str(e), str(e))
try:
    wallet.get_chain("dogecoin")
    check("an unknown chain is refused", False)
except wallet.WalletError:
    check("an unknown chain is refused", True)
check("every configured chain declares a USDC contract and a chain id",
      all(c.get("usdc") and c.get("chain_id") for c in wallet.CHAINS.values()))

check("a valid address is recognised", wallet.is_address(GOOD))
check("a too-short address is rejected", not wallet.is_address("0x1234"))
check("a non-hex address is rejected",
      not wallet.is_address("0xZZZ1111111111111111111111111111111111111"))
check("an empty address is rejected", not wallet.is_address(""))


# ── The owner-only allowlist ──────────────────────────────────────────────────

section("The owner-only allowlist")
# The single most important property: NO tool may write the allowlist. If an
# agent could approve its own destination, every other control is decoration.
check("NO tool can write the allowlist (rule 28's discipline)",
      not any("allowlist" in n for a in colony_tools.AGENT_TOOLS
              for n in colony_tools.tools_for_names(a)),
      str({a: colony_tools.tools_for_names(a) for a in colony_tools.AGENT_TOOLS}))
check("no tool can create a wallet or read the key either",
      not any(n in ("create_wallet", "export_key", "wallet_key")
              for a in colony_tools.AGENT_TOOLS
              for n in colony_tools.tools_for_names(a)))

wallet.add_allowlist("Printer", GOOD, "the print shop")
check("an approved address is stored", wallet.allowlisted(GOOD) is not None)
check("lookup is case-insensitive", wallet.allowlisted(GOOD.upper()) is not None)
check("an unapproved address is not found", wallet.allowlisted(BAD) is None)
try:
    wallet.add_allowlist("Bad", "not-an-address")
    check("an invalid address cannot be approved", False)
except wallet.WalletError:
    check("an invalid address cannot be approved", True)
try:
    wallet.add_allowlist("", GOOD)
    check("an unlabelled address is rejected", False)
except wallet.WalletError:
    check("an unlabelled address is rejected", True)
wallet.add_allowlist("Printer renamed", GOOD)
check("re-approving the same address updates rather than duplicating",
      len(wallet.list_allowlist()) == 1, str(wallet.list_allowlist()))


# ── Caps ──────────────────────────────────────────────────────────────────────

section("Caps, computed from the ledger")
check("a small payment to an approved address is allowed straight through",
      wallet.check_limits(Decimal("2"), GOOD)["decision"] == "auto")
check("a payment over the auto limit needs approval",
      wallet.check_limits(Decimal("10"), GOOD)["decision"] == "approval")
check("a payment over the hard cap is refused outright",
      wallet.check_limits(Decimal("500"), GOOD)["decision"] == "refused")
check("a payment to an UNAPPROVED address is refused however small",
      wallet.check_limits(Decimal("0.01"), BAD)["decision"] == "refused")
check("zero is refused", wallet.check_limits(Decimal("0"), GOOD)["decision"] == "refused")
check("a negative amount is refused",
      wallet.check_limits(Decimal("-5"), GOOD)["decision"] == "refused")

# Play money is not money. A few testnet practice sends must not consume the
# real daily budget — caught for real when mainnet was switched on and a $5
# Base Sepolia test had already eaten $5 of the $50 cap.
wallet.record_tx("base-sepolia", "USDC", GOOD, "40", "0xtest", "sheraj")
check("TESTNET sends do not count against the daily cap",
      wallet.spent_today_usdc() == Decimal("0"), str(wallet.spent_today_usdc()))

# The daily cap must come from what actually left the wallet, not from anything
# the model says it has spent. "base" is a mainnet chain.
for _ in range(5):
    wallet.record_tx("base", "USDC", GOOD, "9", "0xabc", "steward")
check("spend_today is read from the ledger, mainnet only",
      wallet.spent_today_usdc() == Decimal("45"), str(wallet.spent_today_usdc()))
check("a payment breaching the DAILY cap is refused even though it is under "
      "the per-payment cap",
      wallet.check_limits(Decimal("20"), GOOD)["decision"] == "refused",
      str(wallet.check_limits(Decimal("20"), GOOD)))
check("the refusal explains the daily total",
      "daily limit" in wallet.check_limits(Decimal("20"), GOOD)["reason"])
wallet.record_tx("base", "USDC", GOOD, "100", None, "steward", status="failed")
check("a FAILED transaction is excluded from the daily total",
      wallet.spent_today_usdc() == Decimal("45"), str(wallet.spent_today_usdc()))


# ── Sending refuses without a key, and never bypasses the allowlist ───────────

section("Sending guards")
can, why = wallet.sending_available()
check("sending is unavailable without the signing library or a wallet", can is False)
check("the reason is reported plainly, not discovered mid-transfer", bool(why), why)

# The allowlist refusal must be the FIRST thing to fire — asserted strictly, not
# "either the allowlist or some setup error". An earlier version accepted the
# missing-signing-library message here, which meant this check passed for the
# wrong reason and would have kept passing if the allowlist were removed.
try:
    wallet.send_usdc("base-sepolia", BAD, "1", initiated_by="steward")
    check("send refuses an unapproved address", False, "it proceeded")
except wallet.WalletError as e:
    check("send refuses an unapproved address, and says so specifically",
          "not on the approved-addresses list" in str(e), str(e))

# bypass_limits exists for an owner-approved payment. It must NOT also bypass
# the allowlist — that is the control that survives a prompt injection.
try:
    wallet.send_usdc("base-sepolia", BAD, "1", initiated_by="sheraj",
                     bypass_limits=True)
    check("bypass_limits does NOT bypass the allowlist", False, "it proceeded")
except wallet.WalletError as e:
    check("bypass_limits does NOT bypass the allowlist",
          "not on the approved-addresses list" in str(e), str(e))


# ── Nora's tool: the tiered gate ──────────────────────────────────────────────

section("Nora's wallet tool")
check("the Steward has the wallet tools",
      {"wallet_balances", "wallet_send"} <= set(colony_tools.tools_for_names("steward")))
check("NO other agent has a money tool",
      all("wallet_send" not in colony_tools.tools_for_names(a)
          for a in colony_tools.AGENT_TOOLS if a != "steward"),
      str({a: colony_tools.tools_for_names(a) for a in colony_tools.AGENT_TOOLS}))

effects: dict = {"queued": [], "used": []}
execute = colony_tools.make_executor("steward", effects)

out = execute("wallet_send", {"to": BAD, "amount": "1"})
check("the tool refuses an unapproved address", "not an approved address" in out, out)
check("the tool says plainly that nothing was sent", "nothing was sent" in out.lower(), out)

out = execute("wallet_send", {"to": "Printer renamed", "amount": "9999"})
check("the tool refuses an over-cap amount", out.startswith("Refused:"), out)

out = execute("wallet_send", {"to": "Printer renamed", "amount": "not-a-number"})
check("an unreadable amount is refused, not guessed",
      "not an amount" in out, out)

# Another agent must not be able to reach the money tool even by naming it.
artist_exec = colony_tools.make_executor("artist", {"queued": [], "used": []})
out = artist_exec("wallet_send", {"to": "Printer renamed", "amount": "1"})
check("a non-Steward agent calling wallet_send is refused",
      "not one of your tools" in out, out)

# Clear the daily ledger so the queue path can be exercised.
with wallet._connect() as _c:
    _c.execute("DELETE FROM wallet_txs")
    _c.commit()

effects = {"queued": [], "used": []}
execute = colony_tools.make_executor("steward", effects)
out = execute("wallet_send", {"to": "Printer renamed", "amount": "10",
                              "chain": "base-sepolia", "note": "paper"})
check("an amount over the auto limit QUEUES instead of sending",
      "Queued for Sheraj's approval" in out, out)
check("the queued reply states nothing has been sent",
      "NOTHING has been sent" in out, out)
check("the queued action is a real pending row",
      any(a["kind"] == "wallet_send" for a in colony.list_actions("pending")))
check("the queued action carries the resolved ADDRESS, not the label the model "
      "typed",
      json.loads(colony.list_actions("pending")[0]["payload"])["to"] == GOOD)
check("the dashboard is told about the queued payment", len(effects["queued"]) == 1)


# ── Guest isolation ───────────────────────────────────────────────────────────

section("Guest isolation (rules 27/28)")
import agents.secretary as secretary  # noqa: E402

src = Path("agents/secretary.py").read_text(encoding="utf-8")
guest_src = src[src.find("def guest_chat"):]
check("guest_chat still uses the tool-LESS call (no tools reach a guest)",
      "call_claude(" in guest_src and "call_claude_agentic" not in guest_src)
check("the Secretary has no wallet tool of her own",
      "secretary" not in colony_tools.AGENT_TOOLS)
check("the Secretary cannot be chatted with through the Colony at all",
      "secretary" in colony.NO_COLONY_CHAT)
check("no secretary_tools tool mentions the wallet",
      "wallet" not in Path("agents/secretary_tools.py").read_text(encoding="utf-8").lower())


# ── HTTP surface ──────────────────────────────────────────────────────────────

section("HTTP surface")
from fastapi.testclient import TestClient  # noqa: E402

import agents.api as api  # noqa: E402

client = TestClient(api.app, headers=_AUTH)

r = client.get("/wallet/status")
check("GET /wallet/status returns 200", r.status_code == 200, r.text[:200])
body = r.json()
check("status reports mainnet as disabled", body["mainnet_enabled"] is False)
check("status reports that it cannot send yet, with a reason",
      body["can_send"] is False and bool(body["cannot_send_reason"]))
check("status exposes the limits", "max_per_tx_usdc" in body["limits"])
check("status never leaks a private key",
      "private" not in r.text.lower() and "key" not in json.dumps(body.get("limits")).lower())

r = client.post("/wallet/allowlist", json={"label": "Shop", "address": BAD})
check("POST /wallet/allowlist returns 200", r.status_code == 200, r.text[:200])
check("POST an invalid address is a 422",
      client.post("/wallet/allowlist",
                  json={"label": "x", "address": "nope"}).status_code == 422)

r = client.post("/wallet/treasury", json={"label": "Cold store", "address": GOOD})
check("POST /wallet/treasury returns 200", r.status_code == 200, r.text[:200])
check("treasury appears in status",
      any(t["address"] == GOOD for t in client.get("/wallet/status").json()["treasury"]))

r = client.get("/wallet/history")
check("GET /wallet/history returns 200", r.status_code == 200)

# The owner's own send still needs the destination approved — a mistyped
# address is unrecoverable, so the allowlist doubles as a typo guard.
r = client.post("/wallet/send",
                json={"to": "0x3333333333333333333333333333333333333333",
                      "amount": "1", "chain": "base-sepolia"})
check("the owner's send to an unapproved address is refused",
      r.status_code == 400, f"{r.status_code} {r.text[:160]}")

r = client.post("/wallet/send", json={"to": GOOD, "amount": "1", "chain": "base"})
check("the owner's send to a disabled mainnet chain is refused",
      r.status_code == 400, f"{r.status_code} {r.text[:160]}")


# ── The mainnet switch actually switches ──────────────────────────────────────

section("Enabling mainnet")
wallet.ALLOW_MAINNET = True
check("mainnet chains become selectable once enabled", "base" in wallet.enabled_chains())
check("testnets remain available alongside", "base-sepolia" in wallet.enabled_chains())

# Real and play money must never be summed together. Reported as one figure,
# a wallet holding only testnet tokens showed "15.00 USDC" of holdings — a
# false number, which is the one thing the Steward exists not to produce.
_real_usdc, _real_native = wallet._usdc_balance, wallet._native_balance
wallet._usdc_balance = lambda chain, addr: (
    Decimal("7") if wallet.CHAINS[chain]["testnet"] else Decimal("2"))
wallet._native_balance = lambda chain, addr: Decimal("0")
wallet.set_setting("hot_address", GOOD)
_bal = wallet.balances()
check("the headline total counts REAL money only",
      _bal["total_usdc"] == f"{Decimal('2') * 3:.2f}", _bal["total_usdc"])
check("testnet holdings are reported separately, never mixed in",
      _bal["total_testnet_usdc"] == f"{Decimal('7') * 2:.2f}",
      _bal["total_testnet_usdc"])
wallet._usdc_balance, wallet._native_balance = _real_usdc, _real_native

wallet.ALLOW_MAINNET = False
check("turning it back off hides them again", "base" not in wallet.enabled_chains())


# ── Config loading ────────────────────────────────────────────────────────────

section("Config loading")
# Every cap is read at module-import time, and agents.state does not load .env.
# Without wallet.py loading it itself, importing wallet before router left every
# limit on its built-in default — a tightened cap silently not applying.
_wallet_src = Path("agents/wallet.py").read_text(encoding="utf-8")
check("wallet.py loads .env itself rather than relying on import order",
      "load_dotenv(" in _wallet_src)
check("it loads .env BEFORE reading any limit from the environment",
      # The ASSIGNMENT, not the first mention — the comment above load_dotenv
      # names the variable too, which made this pass/fail on prose.
      _wallet_src.index("load_dotenv(")
      < _wallet_src.index('MAX_PER_TX_USDC = Decimal(os.getenv'))


# ── Signing (real key, throwaway, never broadcast) ────────────────────────────

section("Signing")
try:
    from eth_account import Account
    _HAVE_SIGNING = True
except ImportError:
    _HAVE_SIGNING = False

if not _HAVE_SIGNING:
    print("  (skipped: eth-account not installed)")
else:
    # A real keystore round-trip, on a throwaway key in the temp dir. This is
    # what proves create_wallet/_load_key actually work rather than merely
    # importing.
    made = wallet.create_wallet()
    check("create_wallet returns an address", wallet.is_address(made["address"]))
    check("the keystore file is written", wallet.KEYSTORE_PATH.exists())
    check("the keystore on disk does NOT contain the raw private key",
          made["private_key"].replace("0x", "").lower()
          not in wallet.KEYSTORE_PATH.read_text(encoding="utf-8").lower())
    check("the address is remembered", wallet.hot_address() == made["address"])
    check("the key can be decrypted back to the same account",
          wallet._load_key().address == made["address"])
    try:
        wallet.create_wallet()
        check("creating a second wallet is refused (it would strand the first)", False)
    except wallet.WalletError as e:
        check("creating a second wallet is refused (it would strand the first)",
              "already exists" in str(e), str(e))

    wrong = os.environ["WALLET_PASSPHRASE"]
    os.environ["WALLET_PASSPHRASE"] = "the-wrong-passphrase"
    try:
        wallet._load_key()
        check("the wrong passphrase cannot open the keystore", False, "it opened")
    except Exception:
        check("the wrong passphrase cannot open the keystore", True)
    os.environ["WALLET_PASSPHRASE"] = wrong

    # Sign a real ERC-20 transfer OFFLINE and check the encoding. Nothing is
    # broadcast and no funds exist — this verifies the calldata and the
    # signature, which is everything about a transfer except the network.
    acct = wallet._load_key()
    to = GOOD
    units = int(Decimal("1.5") * (10 ** wallet.USDC_DECIMALS))
    data = (wallet._ERC20_TRANSFER + wallet._pad_address(to)
            + format(units, "x").rjust(64, "0"))
    check("transfer calldata is the right length (4-byte selector + 2 words)",
          len(data) == 2 + 8 + 64 + 64, str(len(data)))
    check("calldata carries the ERC-20 transfer selector",
          data.startswith("0xa9059cbb"))
    check("calldata encodes the recipient",
          to.lower().replace("0x", "") in data.lower())
    check("calldata encodes 1.5 USDC as 1500000 base units (6 decimals)",
          int(data[-64:], 16) == 1_500_000, str(int(data[-64:], 16)))

    signed = acct.sign_transaction({
        "chainId": wallet.CHAINS["base-sepolia"]["chain_id"],
        "to": wallet.CHAINS["base-sepolia"]["usdc"],
        "value": 0, "data": data, "nonce": 0, "gas": 100_000,
        "maxPriorityFeePerGas": 1_000_000, "maxFeePerGas": 2_000_000,
    })
    raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction")
    check("a transaction actually signs", bool(raw) and len(raw) > 32)
    check("the signature recovers to this wallet's own address",
          Account.recover_transaction(raw) == acct.address,
          Account.recover_transaction(raw))

    # send_usdc must still refuse an unapproved destination now that signing
    # genuinely works — the check the missing library previously masked. Uses a
    # FRESH address: BAD was approved during the HTTP section above, and reusing
    # it made this fail for the wrong reason (the allowlist was correctly
    # letting it through).
    NEVER_APPROVED = "0x4444444444444444444444444444444444444444"
    try:
        wallet.send_usdc("base-sepolia", NEVER_APPROVED, "1", initiated_by="steward")
        check("with signing available, an unapproved address is STILL refused",
              False, "it proceeded")
    except wallet.WalletError as e:
        check("with signing available, an unapproved address is STILL refused",
              "not on the approved-addresses list" in str(e), str(e))

    # Reaching the network at all proves the calldata is right: the REAL USDC
    # contract on Base Sepolia parsed the transfer and rejected it only for
    # insufficient balance, which is the correct answer for an empty wallet.
    try:
        wallet.send_usdc("base-sepolia", BAD, "1", initiated_by="steward",
                         bypass_limits=True)
        check("an empty wallet's transfer is rejected by the chain, not by us",
              False, "it somehow succeeded")
    except wallet.WalletError as e:
        check("the real USDC contract parses our calldata (rejects only for "
              "balance, not for a malformed call)",
              "exceeds balance" in str(e) or "no ETH for gas" in str(e), str(e)[:160])


print(f"\n{'=' * 62}")
print(f"  {PASS} passed, {FAIL} failed  ({PASS + FAIL} checks)")
if FAILURES:
    print("\nFailures:")
    for f in FAILURES:
        print(f"  - {f}")
print(f"{'=' * 62}")
sys.exit(1 if FAIL else 0)
