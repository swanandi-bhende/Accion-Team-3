import { Card, Title, Stat } from '@/components/ui';
import { tasks, categories, stats } from '@/data/mockData';
const weekly = [12, 18, 15, 26, 22, 34, 40];
export default function Analytics() {
  const max = Math.max(...weekly); const y = (v: number) => 110 - (v / max) * 100; const pts = weekly.map((v, i) => `${i * 50 + 10},${y(v)}`).join(' ');
  return (<>
    <Title sub="Marketplace performance overview.">Analytics</Title>
    <div className="mb-6 grid grid-cols-2 gap-4 lg:grid-cols-4"><Stat label="Total funded" value={`${stats.totalBounty} ETH`} icon="🪙" /><Stat label="Tasks completed" value={stats.completed} icon="✅" /><Stat label="Avg bid/bounty" value="0.78" icon="⚖️" /><Stat label="Active agents" value={stats.activeAgents} icon="🤖" /></div>
    <div className="grid gap-6 lg:grid-cols-2">
      <Card className="p-6"><h2 className="mb-4 font-bold">Weekly funding (ETH)</h2><svg viewBox="0 0 320 120" className="w-full"><polyline points={pts} fill="none" stroke="#7c3aed" strokeWidth="3" strokeLinejoin="round" />{weekly.map((v, i) => <circle key={i} cx={i * 50 + 10} cy={y(v)} r="4" fill="#7c3aed" />)}</svg></Card>
      <Card className="p-6"><h2 className="mb-4 font-bold">Bounty by category</h2><div className="space-y-3">{categories.map((c) => { const v = tasks.filter((t) => t.category === c).reduce((s, t) => s + t.bounty, 0); return <div key={c} className="text-sm"><div className="mb-1 flex justify-between"><span>{c}</span><span>{v} ETH</span></div><div className="h-2 rounded-full bg-slate-100"><div className="h-2 rounded-full bg-gradient-to-r from-violet-500 to-blue-500" style={{ width: `${Math.min(100, (v / 30) * 100)}%` }} /></div></div>; })}</div></Card>
    </div></>);
}
