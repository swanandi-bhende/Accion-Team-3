'use client';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { Card, Badge, TaskTable } from '@/components/ui';
import { agents, tasks, activities } from '@/data/mockData';
export default function AgentDetail() {
  const { id } = useParams<{ id: string }>(); const a = agents.find((x) => x.id === +id);
  if (!a) return <p>Agent not found. <Link href="/agents" className="text-violet-600">Back</Link></p>;
  const m = [['Total bids', a.bids], ['Bids won', a.won], ['Tasks completed', a.done], ['Tasks failed', a.failed], ['Success rate', `${a.success}%`], ['Avg completion', a.avg], ['Total earnings', `${a.earnings} ETH`]];
  return (
    <div className="space-y-6">
      <Link href="/agents" className="text-sm text-violet-600">← Agents</Link>
      <Card className="flex flex-wrap items-center gap-5 p-6">
        <span className="grid h-16 w-16 place-items-center rounded-full bg-violet-100 text-3xl">🤖</span>
        <div className="flex-1"><h1 className="text-2xl font-bold">{a.name}</h1><div className="text-sm text-slate-500">Wallet: {a.wallet}</div><div className="mt-2 flex flex-wrap gap-1.5">{a.caps.map((c) => <Badge key={c} tone="x">{c}</Badge>)}</div></div>
        <div className="text-right"><Badge>{a.status}</Badge><div className="mt-2 text-2xl font-bold text-amber-500">★ {a.rep} <span className="text-sm font-normal text-slate-400">/ 5</span></div></div>
      </Card>
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">{m.map(([k, v]) => <Card key={k as string} className="p-4"><div className="text-xs text-slate-400">{k}</div><div className="text-xl font-bold">{v}</div></Card>)}</div>
      <Card><h2 className="p-5 font-bold">Recent Tasks & Bids</h2><TaskTable list={tasks.slice(0, 4)} /></Card>
      <Card className="p-5"><h2 className="mb-4 font-bold">Activity Timeline</h2><ul className="space-y-3 border-l-2 border-violet-100 pl-4">{activities.map((x, i) => <li key={i} className="text-sm"><span className="-ml-[25px] mr-2 inline-block h-3 w-3 rounded-full bg-violet-500 align-middle" />{x.text} <span className="text-xs text-slate-400">· {x.time}</span></li>)}</ul></Card>
    </div>
  );
}
