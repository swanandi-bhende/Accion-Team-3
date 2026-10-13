'use client';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useState } from 'react';
import { Card, Badge, Btn } from '@/components/ui';
import { useApp } from '@/components/Shell';
import { tasks, bids } from '@/data/mockData';
const steps = ['Bidding', 'Assigned', 'Executing', 'Verified', 'Settled'];
export default function TaskDetail() {
  const { id } = useParams<{ id: string }>(); const { toast } = useApp(); const [modal, setModal] = useState(false); const [funded, setFunded] = useState(false);
  const t = tasks.find((x) => x.id === +id); if (!t) return <p>Task not found. <Link href="/marketplace" className="text-violet-600">Back</Link></p>;
  const cur = t.status === 'Open' ? 0 : t.status === 'In Progress' ? 2 : t.status === 'Completed' ? 4 : -1;
  const isFunded = t.funded || funded;
  return (
    <div className="space-y-6">
      <Link href="/marketplace" className="text-sm text-violet-600">← Task Marketplace</Link>
      <Card className="p-6"><div className="flex flex-wrap items-start justify-between gap-4">
        <div><div className="text-sm text-slate-400">Task #{t.id}</div><h1 className="text-2xl font-bold">{t.title}</h1><p className="mt-2 max-w-2xl text-slate-600">{t.description}</p>
          <div className="mt-3 flex flex-wrap gap-2"><Badge>{t.category}</Badge><Badge>{t.status}</Badge>{t.caps.map((c) => <Badge key={c} tone="Submitted">{c}</Badge>)}</div></div>
        <div className="text-right"><div className="text-3xl font-extrabold">{t.bounty} ETH</div><Btn className="mt-3" onClick={() => (isFunded ? toast('Already funded') : setModal(true))}>{isFunded ? '✓ Funded' : 'Fund Bounty'}</Btn></div></div>
        <dl className="mt-6 grid grid-cols-2 gap-4 text-sm md:grid-cols-5">{[['Deadline', t.deadline], ['Funding', isFunded ? 'Funded' : 'Unfunded'], ['Bids', t.bids], ['Creator', t.creator], ['Created', t.created]].map(([k, v]) => <div key={k as string}><dt className="text-slate-400">{k}</dt><dd className="font-semibold">{v}</dd></div>)}</dl>
      </Card>
      <Card className="p-6"><h2 className="mb-4 font-bold">Execution Timeline</h2>
        <ol className="flex items-center">{steps.map((s, i) => <li key={s} className="flex flex-1 items-center last:flex-none"><div className="flex flex-col items-center gap-1"><span className={`grid h-9 w-9 place-items-center rounded-full text-sm font-bold ${i < cur ? 'bg-emerald-500 text-white' : i === cur ? 'bg-violet-600 text-white ring-4 ring-violet-100' : 'bg-slate-100 text-slate-400'}`}>{i < cur ? '✓' : i + 1}</span><span className="text-xs">{s}</span></div>{i < 4 && <div className={`mx-2 h-0.5 flex-1 ${i < cur ? 'bg-emerald-500' : 'bg-slate-200'}`} />}</li>)}</ol></Card>
      <div className="grid gap-6 lg:grid-cols-[1fr_300px]">
        <Card className="p-6"><h2 className="mb-4 font-bold">Bids</h2><div className="space-y-3">{bids.map((b) => <Link key={b.agent} href={`/agents/${b.agent}`} className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-slate-100 p-4 hover:bg-violet-50/40"><div className="font-semibold">Agent #{String(b.agent).padStart(2, '0')}</div><div className="text-sm">Bid: <b>{b.amount} ETH</b></div><div className="text-sm text-slate-500">ETA {b.eta}</div><div className="text-sm text-amber-500">★ {b.rep}</div><Badge>{b.status}</Badge></Link>)}</div></Card>
        <Card className="p-6"><h2 className="mb-4 font-bold">Assigned Agent</h2>{t.agent ? <div className="flex items-center gap-3"><span className="grid h-10 w-10 place-items-center rounded-full bg-violet-100">🤖</span><b>{t.agent}</b></div> : <p className="text-sm text-slate-500">No agent assigned yet. Bidding is open.</p>}</Card>
      </div>
      {modal && <div className="fixed inset-0 z-40 grid place-items-center bg-slate-900/40"><Card className="w-96 p-6"><h3 className="text-lg font-bold">Fund Bounty</h3><p className="my-3 text-sm text-slate-600">Deposit <b>{t.bounty} ETH</b> into escrow for Task #{t.id} on Sepolia. (Simulated — no transaction is sent.)</p><div className="flex justify-end gap-2"><Btn variant="ghost" onClick={() => setModal(false)}>Cancel</Btn><Btn onClick={() => { setModal(false); setFunded(true); toast(`Simulated: funded ${t.bounty} ETH`); }}>Confirm</Btn></div></Card></div>}
    </div>
  );
}
