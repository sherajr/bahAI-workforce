# Rules 42-49 — the project wallet

Canonical numbered hard rules for this subsystem. **Numbers are permanent — never
renumber, only append.** Cited from code comments across the repo.
Loaded on demand from the root `AGENTS.md` routing table; do not copy these
into CLAUDE.md or the root AGENTS.md.

## Rules 42–49 — the project wallet (`agents/wallet.py`, Nora's domain)

A cross-chain wallet that receives giving, holds a treasury for the PeaceAntz
DAO idea, shows holdings in the Steward's report, and pays real expenses — with
Nora able to send within a hard cap (owner ask 2026-08-14). The irreversibility
risk was put to Sheraj plainly first and he chose the most capable option at
every step, so these rules exist to make that choice as safe as it honestly can
be. Verify with `scripts/test_wallet.py` (never touches a network or a key).

**This is the one part of the repo where a mistake cannot be undone.** Every
other failure can be re-run, re-scored or deleted. Weight changes accordingly.

42. **The destination allowlist is OWNER-ONLY and is the control that survives a
    prompt injection.** Exactly rule 28's discipline: no tool in `colony_tools`
    writes `wallet_allowlist`, and the suite asserts that no tool name contains
    "allowlist" and that none can create a wallet or read a key. A fully
    compromised Nora can still only move money to addresses Sheraj typed in
    himself. `bypass_limits=True` (for an owner-APPROVED queued payment) skips
    the caps but NEVER the allowlist or the token-contract check.
43. **Caps are computed from the on-chain ledger, never from the model.**
    `check_limits` reads `spent_today_usdc()` out of `wallet_txs` (excluding
    failed sends). Three tiers, all decided in code: at or under
    `WALLET_AUTO_SEND_USDC` Nora sends directly; above that up to
    `WALLET_MAX_PER_TX_USDC` it queues in the existing `colony_actions` queue;
    over that, or over `WALLET_DAILY_CAP_USDC`, it is refused. `wallet_send` is
    deliberately NOT in `GATED_KINDS` — that would turn every payment into an
    approval and remove the autonomy Sheraj asked for; it has its own tiered
    gate in `_h_wallet_send` and lives in `MONEY_KINDS` instead.
44. **The agent may only ever send USDC.** A stablecoin makes a dollar cap mean
    what it says with no price feed to go stale or be manipulated. There is NO
    native-token send at all — not for the agent and not for the owner;
    `send_usdc` is the only spending path in the module, and the native balance
    exists solely to pay gas. (Consequence learned in practice: a wallet funded
    only with test ETH cannot transfer yet — testnet USDC has to be obtained
    separately before the send path can be exercised.) Only the Steward has a
    money tool; the suite asserts no other agent does, and that a non-Steward
    calling `wallet_send` is refused by the executor's tool-membership check.
45. **Token contracts are verified ON-CHAIN before every transfer.**
    `verify_token()` calls `symbol()`/`decimals()` and refuses unless it is
    really USDC with 6 decimals. The addresses in `CHAINS` were checked live
    against each RPC on 2026-08-14 — but a hardcoded token address is exactly
    the thing never to trust from memory, because a wrong one means transferring
    to something that is not the token and the funds are gone with no error.
46. **Mainnet is opt-in** (`WALLET_ALLOW_MAINNET=true`). Default off, so only
    testnets are selectable and the whole feature can be exercised for real with
    nothing at risk. `get_chain` refuses a disabled mainnet chain with an
    explanation rather than silently falling back to a testnet.
47. **Two wallets, and only one is reachable by an agent.** `hot` holds a small
    float and its key lives encrypted in `private/wallet/` (gitignored,
    `WALLET_PASSPHRASE` from .env). `treasury` is a list of WATCH-ONLY addresses
    Sheraj controls elsewhere — Nora reads them and has no key, so the DAO
    treasury has a home outside the LLM's blast radius. Never merge the two.
48. **An unreachable chain is reported as unreachable, never as zero.** A
    balance of 0 that actually means "the RPC was down" would make the Steward's
    report quietly wrong, which is the one thing the Steward exists not to be.
    Holds in `balances()`, in Nora's tool output, and in the UI.
49. **Signing is a HARD dependency, declared up front.** `eth-account` only (not
    full web3.py — reads are raw JSON-RPC, so the key-touching surface stays
    minimal). `sending_available()` reports its absence, a missing wallet, or a
    missing passphrase BEFORE anything is attempted — the same preflight
    discipline as PyAV in rule 33a. Never hand-roll the crypto.

