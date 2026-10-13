'use client';
import { Card, Title, Btn, Badge } from '@/components/ui';
import { useApp } from '@/components/Shell';
import { stats, transactions } from '@/data/mockData';
export default function Wallet() {
  const { connected, toggle, toast } = useApp();
  return (<>
    <Title sub="Frontend-only mock wallet.">Wallet</Title>
    {!connected ? <Card className="p-10 text-center"><p className="mb-4 text-slate-500">No wallet connected.</p><Btn onClick={toggle}>Connect Wallet</Btn></Card> : (<div className="space-y-6">
      <Card className="flex flex-wrap items-center justify-between gap-4 bg-gradient-to-r from-violet-600 to-blue-500 p-6 text-white"><div><div className="text-sm opacity-80">Connected wallet · Sepolia</div><div className="font-mono text-lg">{stats.address}</div><div className="mt-2 text-4xl font-extrabold">{stats.balance} ETH</div></div><Btn variant="ghost" onClick={toggle}>Disconnect</Btn></Card>
      <Card><h2 className="p-5 font-bold">Funding & Settlement Activity</h2><table className="w-full text-left text-sm"><tbody>{transactions.map((t) => <tr key={t.hash} className="border-t border-slate-100"><td className="px-5 py-3"><Badge tone={t.type === 'Funding' ? 'In Progress' : 'Completed'}>{t.type}</Badge></td><td className="px-4 py-3">{t.desc}</td><td className={`px-4 py-3 font-semibold ${t.amount > 0 ? 'text-emerald-600' : 'text-slate-800'}`}>{t.amount > 0 ? '+' : ''}{t.amount} ETH</td><td className="px-4 py-3 text-slate-400">{t.time}</td><td className="px-4 py-3"><button className="text-violet-600" onClick={() => toast(`View Transaction ${t.hash} (mock)`)}>View Transaction</button></td></tr>)}</tbody></table></Card></div>)}</>);
}
