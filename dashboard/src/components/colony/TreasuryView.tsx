import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Copy, ExternalLink, Plus, RefreshCw, Trash2, Wallet } from "lucide-react";
import { api } from "../../lib/api";
import type { AllowlistEntry, ChainBalance, WalletBalances, WalletStatus, WalletSendResult, WalletTx } from "../../lib/types";
import { cn, formatDate } from "../../lib/utils";
import { BadgePill, Button, Card, CardContent, CardHeader, CardTitle, ErrorNote } from "../ui";

/**
 * The project wallet — Nora's domain.
 *
 * Deliberately blunt about risk rather than reassuring: crypto transfers are
 * irreversible, so this screen states what is switched on, what the limits are,
 * and what Nora can and cannot do, without the user having to read any code.
 */
export function TreasuryView() {
  const status = useQuery({ queryKey: ["wallet-status"], queryFn: api.getWalletStatus });
  const balances = useQuery({
    queryKey: ["wallet-balances"],
    queryFn: api.getWalletBalances,
    enabled: !!status.data?.exists,
    refetchInterval: 60_000,
  });
  // Wrapped, not passed directly: react-query hands the query context as the
  // first argument, which would land in getWalletHistory's `limit`.
  const history = useQuery({
    queryKey: ["wallet-history"],
    queryFn: () => api.getWalletHistory(),
  });

  const refreshAll = () => {
    status.refetch(); balances.refetch(); history.refetch();
  };

  const s = status.data;

  return (
    <div className="max-w-4xl space-y-5">
      {status.isError && (
        <ErrorNote>Could not read the wallet: {(status.error as Error).message}</ErrorNote>
      )}

      {s && !s.mainnet_enabled && (
        <div className="flex gap-2.5 rounded-lg border border-sky-400/30 bg-sky-400/5 px-4 py-3
                        text-sm text-sky-200/90">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>
            <strong>Practice mode.</strong> Only test networks are switched on, so nothing
            here is real money — you can try everything safely. To use real funds, add{" "}
            <span className="font-mono text-xs">WALLET_ALLOW_MAINNET=true</span> to your{" "}
            <span className="font-mono text-xs">.env</span> and restart.
          </span>
        </div>
      )}

      {s && !s.exists && <CreateWallet onCreated={refreshAll} canSign={s.can_send}
                                       reason={s.cannot_send_reason} />}

      {s?.exists && (
        <>
          <Holdings
            s={s}
            balances={balances.data}
            loading={balances.isLoading}
            // isFetching, not isLoading: the latter is only true on the very
            // first load, so a manual refresh would give no feedback at all.
            refreshing={balances.isFetching}
            updatedAt={balances.dataUpdatedAt}
            onRefresh={() => balances.refetch()}
          />
          <Limits s={s} />
          <Approved s={s} onChanged={refreshAll} />
          <Treasury s={s} onChanged={refreshAll} />
          <SendForm s={s} onSent={refreshAll} />
          <History rows={history.data?.transactions ?? []} />
        </>
      )}
    </div>
  );
}

function CreateWallet({ onCreated, canSign, reason }: {
  onCreated: () => void; canSign: boolean; reason: string;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<{ address: string; private_key: string } | null>(null);

  const create = async () => {
    setBusy(true); setError(null);
    try {
      const res = await api.createWallet();
      setCreated(res);
      onCreated();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not create the wallet.");
    } finally { setBusy(false); }
  };

  if (created) {
    return (
      <Card>
        <CardHeader><CardTitle>Write this down now</CardTitle></CardHeader>
        <CardContent className="space-y-3">
          <div className="rounded-lg border border-rose-500/40 bg-rose-500/10 px-4 py-3
                          text-sm text-rose-100">
            This is the only time this key is shown. Anyone who has it can take everything
            in the wallet. Write it on paper and keep it somewhere safe — not in an email,
            a screenshot, or a chat.
          </div>
          <Field label="Address" value={created.address} />
          <Field label="Private key" value={created.private_key} secret />
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader><CardTitle className="flex items-center gap-2">
        <Wallet className="h-4 w-4 text-amber-300" /> Create the project wallet
      </CardTitle></CardHeader>
      <CardContent className="space-y-3">
        <p className="text-sm text-slate-400">
          One address that works across Base, Arbitrum and Optimism. It can receive
          money straight away; sending needs a little of the network's own coin for fees.
        </p>
        {!canSign && reason && (
          <div className="rounded-lg border border-amber-400/30 bg-amber-400/5 px-3 py-2
                          text-xs text-amber-200/90">{reason}</div>
        )}
        {error && <ErrorNote>{error}</ErrorNote>}
        <Button onClick={create} loading={busy} disabled={busy}>Create wallet</Button>
      </CardContent>
    </Card>
  );
}

function Field({ label, value, secret }: { label: string; value: string; secret?: boolean }) {
  const [shown, setShown] = useState(!secret);
  return (
    <div>
      <div className="mb-1 text-xs uppercase tracking-wider text-slate-500">{label}</div>
      <div className="flex items-center gap-2">
        <code className="min-w-0 flex-1 truncate rounded-lg border border-slate-700
                         bg-slate-900/80 px-3 py-2 font-mono text-xs text-slate-200">
          {shown ? value : "•".repeat(48)}
        </code>
        {secret && (
          <button onClick={() => setShown((v) => !v)}
                  className="text-xs text-slate-400 hover:text-slate-200">
            {shown ? "Hide" : "Show"}
          </button>
        )}
        <button onClick={() => navigator.clipboard?.writeText(value)}
                title="Copy" className="text-slate-500 hover:text-slate-300">
          <Copy className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}

function Holdings({ s, balances, loading, refreshing, updatedAt, onRefresh }: {
  s: WalletStatus;
  balances: WalletBalances | undefined;
  loading: boolean;
  refreshing: boolean;
  updatedAt: number;
  onRefresh: () => void;
}) {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-3">
          <CardTitle>Holdings</CardTitle>
          <div className="flex items-center gap-2.5">
            {/* Balances refresh on their own every 60s, so saying WHEN this was
                read matters as much as the button — a number with no timestamp
                gives no way to tell fresh from a minute stale. */}
            {updatedAt > 0 && !refreshing && (
              <span className="text-[11px] text-slate-600">
                as of {new Date(updatedAt).toLocaleTimeString()}
              </span>
            )}
            {refreshing && <span className="text-[11px] text-slate-500">checking…</span>}
            <button
              onClick={onRefresh}
              disabled={refreshing}
              title="Check the networks again now"
              aria-label="Refresh holdings"
              className="rounded-lg border border-slate-700 p-1.5 text-slate-400
                         transition-colors hover:border-slate-600 hover:text-slate-200
                         disabled:opacity-50"
            >
              <RefreshCw className={cn("h-3.5 w-3.5", refreshing && "animate-spin")} />
            </button>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <Field label="Receiving address (safe to share)" value={s.address ?? ""} />
        {loading && <p className="text-sm text-slate-500">Reading the networks…</p>}
        {balances?.chains?.map((c: ChainBalance) => (
          <div key={c.chain}
               className="flex items-center justify-between gap-3 rounded-lg border
                          border-slate-800 bg-slate-950/50 px-3 py-2 text-sm">
            <span className="text-slate-300">
              {c.name} {c.testnet && <BadgePill className="ml-1 border-sky-400/40 bg-sky-400/10 text-sky-300">test</BadgePill>}
            </span>
            {c.reachable ? (
              <span className="font-mono text-xs text-slate-300">
                {Number(c.usdc).toFixed(2)} USDC · {Number(c.native).toFixed(4)} {c.native_symbol}
              </span>
            ) : (
              // An unreachable network is NOT zero, and must never look like it.
              <span className="text-xs text-amber-300/80">couldn't reach it — balance unknown</span>
            )}
          </div>
        ))}
        {balances && (
          <p className="text-sm text-slate-400">
            Real money held:{" "}
            <span className="text-slate-100">{balances.total_usdc} USDC</span>
            {balances.total_testnet_usdc && balances.total_testnet_usdc !== "0.00" && (
              // Never folded into the headline figure — play money reported as
              // holdings is a false number, which is the one thing the Steward
              // exists not to produce.
              <span className="text-slate-600">
                {" "}· plus {balances.total_testnet_usdc} USDC of test-network play money
              </span>
            )}
          </p>
        )}
        {/* Sheraj funded the wallet on Ethereum Sepolia while only Base Sepolia
            was being read, and the screen simply showed zero — which reads as
            "your money is gone" rather than "we don't look there". Naming the
            scope is the difference. */}
        <p className="text-[11px] leading-relaxed text-slate-600">
          Only the networks listed above are checked. The same address also exists on
          every other EVM network — anything sent to it elsewhere is safe, but won't
          show here until that network is added.
        </p>
      </CardContent>
    </Card>
  );
}

function Limits({ s }: { s: WalletStatus }) {
  const l = s.limits;
  return (
    <Card>
      <CardHeader><CardTitle>What Nora may spend</CardTitle></CardHeader>
      <CardContent className="space-y-2 text-sm text-slate-400">
        <p>
          She can pay <strong className="text-slate-200">${l.auto_send_usdc}</strong> on her own.
          Between that and <strong className="text-slate-200">${l.max_per_tx_usdc}</strong> she has
          to ask you first. Above that she cannot pay at all, and she can never send more than{" "}
          <strong className="text-slate-200">${l.daily_cap_usdc}</strong> in a day.
        </p>
        <p className="text-xs text-slate-500">
          ${l.spent_today_usdc} sent today. She can only ever send USDC, only to the approved
          addresses below, and she can never add one herself.
        </p>
        {!s.can_send && s.cannot_send_reason && (
          <div className="rounded-lg border border-amber-400/30 bg-amber-400/5 px-3 py-2
                          text-xs text-amber-200/90">
            Sending is currently switched off: {s.cannot_send_reason}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function AddressList({
  title, blurb, rows, onAdd, onRemove, addLabel,
}: {
  title: string; blurb: string; rows: AllowlistEntry[];
  onAdd: (label: string, address: string) => Promise<void>;
  onRemove: (id: number) => Promise<void>;
  addLabel: string;
}) {
  const [label, setLabel] = useState("");
  const [address, setAddress] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const add = async () => {
    setBusy(true); setError(null);
    try {
      await onAdd(label.trim(), address.trim());
      setLabel(""); setAddress("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not add that.");
    } finally { setBusy(false); }
  };

  return (
    <Card>
      <CardHeader><CardTitle>{title}</CardTitle></CardHeader>
      <CardContent className="space-y-3">
        <p className="text-sm text-slate-400">{blurb}</p>
        {rows.length === 0 && <p className="text-sm text-slate-500">None yet.</p>}
        {rows.map((r) => (
          <div key={r.id} className="flex items-center justify-between gap-3 rounded-lg
                                     border border-slate-800 bg-slate-950/50 px-3 py-2">
            <div className="min-w-0">
              <div className="text-sm text-slate-200">{r.label}</div>
              <code className="block truncate font-mono text-xs text-slate-500">{r.address}</code>
            </div>
            <button onClick={() => onRemove(r.id)}
                    className="shrink-0 text-slate-600 hover:text-rose-400">
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          </div>
        ))}
        <div className="flex flex-wrap gap-2">
          <input value={label} onChange={(e) => setLabel(e.target.value)}
                 placeholder="Name (e.g. Printer)"
                 className="w-40 rounded-lg border border-slate-700 bg-slate-900/80 px-3 py-2
                            text-sm text-slate-200 placeholder:text-slate-600
                            focus:border-amber-400/50 focus:outline-none" />
          <input value={address} onChange={(e) => setAddress(e.target.value)}
                 placeholder="0x…"
                 className="min-w-0 flex-1 rounded-lg border border-slate-700 bg-slate-900/80
                            px-3 py-2 font-mono text-sm text-slate-200
                            placeholder:text-slate-600 focus:border-amber-400/50
                            focus:outline-none" />
          <Button onClick={add} loading={busy}
                  disabled={busy || !label.trim() || !address.trim()}>
            <Plus className="h-3.5 w-3.5" /> {addLabel}
          </Button>
        </div>
        {error && <ErrorNote>{error}</ErrorNote>}
      </CardContent>
    </Card>
  );
}

function Approved({ s, onChanged }: { s: WalletStatus; onChanged: () => void }) {
  return (
    <AddressList
      title="Approved addresses"
      blurb="The only places money can go. You add these yourself — Nora cannot, no matter
             what she is asked. This is also what catches a mistyped address, which would
             otherwise be unrecoverable."
      rows={s.allowlist}
      addLabel="Approve"
      onAdd={async (label, address) => { await api.addAllowlist(label, address); onChanged(); }}
      onRemove={async (id) => { await api.removeAllowlist(id); onChanged(); }}
    />
  );
}

function Treasury({ s, onChanged }: { s: WalletStatus; onChanged: () => void }) {
  return (
    <AddressList
      title="Treasury (watch only)"
      blurb="Addresses you hold elsewhere. Nora reads these and includes them in her
             reporting, but has no key for them and can never spend from them."
      rows={s.treasury}
      addLabel="Watch"
      onAdd={async (label, address) => { await api.addTreasury(label, address); onChanged(); }}
      onRemove={async (id) => { await api.removeTreasury(id); onChanged(); }}
    />
  );
}

function SendForm({ s, onSent }: { s: WalletStatus; onSent: () => void }) {
  const [to, setTo] = useState("");
  const [amount, setAmount] = useState("");
  const [chain, setChain] = useState(s.default_chain);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState<WalletSendResult | null>(null);

  const send = async () => {
    setBusy(true); setError(null); setSent(null);
    try {
      const res = await api.walletSend({ to, amount, chain, note });
      setSent(res); setAmount(""); setNote("");
      onSent();
    } catch (e) {
      setError(e instanceof Error ? e.message : "The payment did not go through.");
    } finally { setBusy(false); }
  };

  return (
    <Card>
      <CardHeader><CardTitle>Send USDC yourself</CardTitle></CardHeader>
      <CardContent className="space-y-3">
        <p className="text-sm text-slate-400">
          Straight from here, with no AI anywhere in the path — the safest way to move
          money. Your own payments aren't limited by Nora's caps, but the address still
          has to be approved above.
        </p>
        <div className="flex flex-wrap gap-2">
          <select value={to} onChange={(e) => setTo(e.target.value)}
                  className="min-w-[10rem] flex-1 rounded-lg border border-slate-700
                             bg-slate-900/80 px-3 py-2 text-sm text-slate-200
                             focus:border-amber-400/50 focus:outline-none">
            <option value="">Choose an approved address…</option>
            {s.allowlist.map((a) => (
              <option key={a.id} value={a.address}>{a.label}</option>
            ))}
          </select>
          <select value={chain} onChange={(e) => setChain(e.target.value)}
                  className="rounded-lg border border-slate-700 bg-slate-900/80 px-3 py-2
                             text-sm text-slate-200 focus:border-amber-400/50 focus:outline-none">
            {s.chains.map((c) => (
              <option key={c.id} value={c.id}>{c.name}</option>
            ))}
          </select>
          <input value={amount} onChange={(e) => setAmount(e.target.value)}
                 placeholder="Amount in USDC" inputMode="decimal"
                 className="w-36 rounded-lg border border-slate-700 bg-slate-900/80 px-3 py-2
                            text-sm text-slate-200 placeholder:text-slate-600
                            focus:border-amber-400/50 focus:outline-none" />
        </div>
        <input value={note} onChange={(e) => setNote(e.target.value)}
               placeholder="What's it for? (optional)"
               className="w-full rounded-lg border border-slate-700 bg-slate-900/80 px-3 py-2
                          text-sm text-slate-200 placeholder:text-slate-600
                          focus:border-amber-400/50 focus:outline-none" />
        <Button onClick={send} loading={busy}
                disabled={busy || !to || !amount || !s.can_send}>
          Send
        </Button>
        {!s.can_send && (
          <p className="text-xs text-amber-300/80">{s.cannot_send_reason}</p>
        )}
        {error && <ErrorNote>{error}</ErrorNote>}
        {sent && (
          <p className="rounded-lg border border-emerald-400/30 bg-emerald-400/5 px-3 py-2
                        text-sm text-emerald-200">
            Sent {sent.amount} USDC to {sent.to_label}.{" "}
            {sent.explorer_url && (
              <a href={sent.explorer_url} target="_blank" rel="noreferrer"
                 className="underline">
                View it <ExternalLink className="inline h-3 w-3" />
              </a>
            )}
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function History({ rows }: { rows: WalletTx[] }) {
  return (
    <Card>
      <CardHeader><CardTitle>Payments</CardTitle></CardHeader>
      <CardContent>
        {rows.length === 0 && <p className="text-sm text-slate-500">Nothing sent yet.</p>}
        <div className="space-y-1.5">
          {rows.map((t) => (
            <div key={t.id} className="flex flex-wrap items-center justify-between gap-2
                                       rounded-lg border border-slate-800 bg-slate-950/50
                                       px-3 py-2 text-sm">
              <div className="min-w-0">
                <div className="text-slate-200">
                  {t.amount} {t.token} → {t.to_label || t.to_address}
                </div>
                <div className="text-xs text-slate-600">
                  {formatDate(t.created_at)} · started by {t.initiated_by}
                  {t.note ? ` · ${t.note}` : ""}
                </div>
              </div>
              <div className="flex items-center gap-2">
                <BadgePill className={t.status === "failed"
                  ? "border-rose-400/40 bg-rose-400/10 text-rose-300"
                  : "border-emerald-400/40 bg-emerald-400/10 text-emerald-300"}>
                  {t.status}
                </BadgePill>
                {t.explorer_url && (
                  <a href={t.explorer_url} target="_blank" rel="noreferrer"
                     className="text-slate-500 hover:text-slate-300">
                    <ExternalLink className="h-3.5 w-3.5" />
                  </a>
                )}
              </div>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
