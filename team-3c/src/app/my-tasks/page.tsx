'use client';
import Link from 'next/link';
import { useState } from 'react';
import { Card, Title, Badge } from '@/components/ui';
import { tasks } from '@/data/mockData';
const tabs = ['All', 'Open', 'In Progress', 'Completed', 'Expired'];
export default function MyTasks() {
  const [tab, setTab] = useState('All'); const list = tasks.filter((t) => t.mine && (tab === 'All' || t.status === tab));
  return (<>
    <Title sub="Tasks you have posted.">My Tasks</Title>
    <div className="mb-4 flex gap-2">{tabs.map((t) => <button key={t} onClick={() => setTab(t)} className={`rounded-lg border px-4 py-1.5 text-sm ${tab === t ? 'border-violet-600 bg-violet-600 text-white' : 'border-slate-200 bg-white text-slate-600'}`}>{t}</button>)}</div>
    <Card className="overflow-x-auto"><table className="w-full text-left text-sm"><thead className="bg-slate-50 text-slate-500"><tr>{['Task', 'Bounty', 'Bids', 'Assigned Agent', 'Deadline', 'Status'].map((h) => <th key={h} className="px-4 py-3 font-medium">{h}</th>)}</tr></thead>
      <tbody>{list.map((t) => <tr key={t.id} className="border-t border-slate-100"><td className="px-4 py-3 font-semibold"><Link href={`/tasks/${t.id}`} className="hover:text-violet-600">{t.title}</Link></td><td className="px-4 py-3">{t.bounty} ETH</td><td className="px-4 py-3">{t.bids}</td><td className="px-4 py-3">{t.agent ?? '—'}</td><td className="px-4 py-3">{t.deadline}</td><td className="px-4 py-3"><Badge>{t.status}</Badge></td></tr>)}
        {!list.length && <tr><td colSpan={6} className="p-8 text-center text-slate-400">Nothing here</td></tr>}</tbody></table></Card></>);
}
