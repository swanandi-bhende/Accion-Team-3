'use client';
import { Suspense, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { Card, Title, TaskTable } from '@/components/ui';
import { tasks, categories } from '@/data/mockData';
const sel = 'rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm';
function Inner() {
  const [q, setQ] = useState(useSearchParams().get('q') ?? ''); const [cat, setCat] = useState('All'); const [st, setSt] = useState('All'); const [min, setMin] = useState(0); const [sort, setSort] = useState('newest'); const [page, setPage] = useState(1);
  const list = tasks.filter((t) => (cat === 'All' || t.category === cat) && (st === 'All' || t.status === st) && t.bounty >= min && (t.title + t.description + t.category).toLowerCase().includes(q.toLowerCase()))
    .sort((a, b) => sort === 'bounty' ? b.bounty - a.bounty : sort === 'bids' ? b.bids - a.bids : b.id - a.id);
  const pages = Math.max(1, Math.ceil(list.length / 5)); const p = Math.min(page, pages);
  const on = (f: (v: any) => void) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => { f(e.target.value); setPage(1); };
  return (<>
    <Title sub="Browse open tasks, view bids and assign the best agent.">Task Marketplace</Title>
    <Card>
      <div className="flex flex-wrap gap-3 p-5">
        <input value={q} onChange={on(setQ)} placeholder="Search tasks..." className={`${sel} min-w-52 flex-1`} />
        <select className={sel} value={cat} onChange={on(setCat)}><option>All</option>{categories.map((c) => <option key={c}>{c}</option>)}</select>
        <select className={sel} value={st} onChange={on(setSt)}>{['All', 'Open', 'In Progress', 'Completed', 'Expired'].map((c) => <option key={c}>{c}</option>)}</select>
        <select className={sel} value={min} onChange={on((v) => setMin(+v))}><option value={0}>Any bounty</option><option value={5}>≥ 5 ETH</option><option value={10}>≥ 10 ETH</option></select>
        <select className={sel} value={sort} onChange={on(setSort)}><option value="newest">Newest</option><option value="bounty">Highest bounty</option><option value="bids">Most bids</option></select>
      </div>
      <TaskTable list={list.slice((p - 1) * 5, p * 5)} />
      <div className="flex items-center justify-between p-4 text-sm text-slate-500"><span>Showing {list.length ? (p - 1) * 5 + 1 : 0}–{Math.min(p * 5, list.length)} of {list.length} tasks</span>
        <div className="flex gap-1">{Array.from({ length: pages }, (_, i) => <button key={i} onClick={() => setPage(i + 1)} className={`h-8 w-8 rounded-lg border ${p === i + 1 ? 'border-violet-600 bg-violet-600 text-white' : 'border-slate-200'}`}>{i + 1}</button>)}</div></div>
    </Card></>);
}
export default function Page() { return <Suspense><Inner /></Suspense>; }
