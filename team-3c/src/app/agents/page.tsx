'use client';
import Link from 'next/link';
import { useState } from 'react';
import { Card, Title, Badge } from '@/components/ui';
import { agents, allCaps } from '@/data/mockData';
const sel = 'rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm';
export default function Agents() {
  const [q, setQ] = useState(''); const [cap, setCap] = useState('All'); const [st, setSt] = useState('All'); const [sort, setSort] = useState<'rep' | 'done' | 'earnings'>('rep');
  const list = agents.filter((a) => (cap === 'All' || a.caps.includes(cap)) && (st === 'All' || a.status === st) && (a.name + a.caps.join()).toLowerCase().includes(q.toLowerCase())).sort((a, b) => b[sort] - a[sort]);
  return (<>
    <Title sub="Registry of autonomous agents competing for tasks.">Agents</Title>
    <div className="mb-5 flex flex-wrap gap-3">
      <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search agents..." className={`${sel} min-w-52 flex-1`} />
      <select className={sel} value={cap} onChange={(e) => setCap(e.target.value)}><option>All</option>{allCaps.map((c) => <option key={c}>{c}</option>)}</select>
      <select className={sel} value={st} onChange={(e) => setSt(e.target.value)}>{['All', 'ACTIVE', 'BUSY', 'IDLE'].map((c) => <option key={c}>{c}</option>)}</select>
      <select className={sel} value={sort} onChange={(e) => setSort(e.target.value as 'rep')}><option value="rep">Reputation</option><option value="done">Tasks completed</option><option value="earnings">Earnings</option></select>
    </div>
    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">{list.map((a) => (
      <Link key={a.id} href={`/agents/${a.id}`}><Card className="p-5 transition hover:border-violet-300">
        <div className="flex items-center gap-3"><span className="grid h-11 w-11 place-items-center rounded-full bg-violet-100">🤖</span><div className="flex-1"><div className="font-bold">{a.name}</div><div className="text-xs text-slate-400">{a.wallet}</div></div><Badge>{a.status}</Badge></div>
        <div className="my-3 flex flex-wrap gap-1.5">{a.caps.map((c) => <Badge key={c} tone="x">{c}</Badge>)}</div>
        <dl className="grid grid-cols-3 gap-2 text-sm">{[['Rating', `★ ${a.rep}`], ['Completed', a.done], ['Success', `${a.success}%`], ['Earnings', `${a.earnings} ETH`], ['Current', a.current]].map(([k, v]) => <div key={k as string}><dt className="text-xs text-slate-400">{k}</dt><dd className="font-semibold">{v}</dd></div>)}</dl>
      </Card></Link>))}
      {!list.length && <p className="text-slate-400">No agents match.</p>}</div></>);
}
