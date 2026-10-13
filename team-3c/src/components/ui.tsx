import Link from 'next/link';
import { tasks } from '@/data/mockData';
export const Card = ({ children, className = '' }: { children: React.ReactNode; className?: string }) => (
  <div className={`rounded-2xl border border-slate-200 bg-white shadow-sm ${className}`}>{children}</div>
);
const tones: Record<string, string> = {
  Open: 'bg-emerald-50 text-emerald-700', 'In Progress': 'bg-blue-50 text-blue-700', Completed: 'bg-violet-50 text-violet-700', Expired: 'bg-slate-100 text-slate-500',
  ACTIVE: 'bg-emerald-50 text-emerald-700', BUSY: 'bg-amber-50 text-amber-700', IDLE: 'bg-slate-100 text-slate-500', Submitted: 'bg-blue-50 text-blue-700',
};
export const Badge = ({ children, tone }: { children: React.ReactNode; tone?: string }) => (
  <span className={`rounded-md px-2.5 py-1 text-xs font-medium ${tones[tone ?? String(children)] ?? 'bg-indigo-50 text-indigo-700'}`}>{children}</span>
);
export const Btn = ({ children, onClick, variant = 'primary', className = '' }: { children: React.ReactNode; onClick?: () => void; variant?: 'primary' | 'ghost'; className?: string }) => (
  <button onClick={onClick} className={`rounded-lg px-4 py-2 text-sm font-semibold transition ${variant === 'primary' ? 'bg-violet-600 text-white hover:bg-violet-700' : 'border border-slate-200 bg-white text-slate-800 hover:bg-slate-50'} ${className}`}>{children}</button>
);
export const Title = ({ children, sub }: { children: React.ReactNode; sub?: string }) => (
  <div className="mb-5"><h1 className="text-2xl font-bold text-slate-900">{children}</h1>{sub && <p className="text-sm text-slate-500">{sub}</p>}</div>
);
export const Stat = ({ label, value, icon }: { label: string; value: string | number; icon: string }) => (
  <Card className="flex items-center gap-4 p-5">
    <div className="grid h-12 w-12 place-items-center rounded-xl bg-indigo-50 text-xl">{icon}</div>
    <div><div className="text-sm text-slate-500">{label}</div><div className="text-2xl font-bold text-slate-900">{value}</div></div>
  </Card>
);
export function TaskTable({ list }: { list: typeof tasks }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead className="bg-slate-50 text-slate-500"><tr>{['Task', 'Category', 'Bounty', 'Deadline', 'Bids', 'Status'].map((h) => <th key={h} className="px-4 py-3 font-medium">{h}</th>)}</tr></thead>
        <tbody>
          {list.map((t) => (
            <tr key={t.id} className="border-t border-slate-100 hover:bg-violet-50/40">
              <td className="px-4 py-3"><Link href={`/tasks/${t.id}`} className="block"><div className="font-semibold text-slate-900">{t.title} <span className="text-xs font-normal text-slate-400">#{t.id}</span></div><div className="max-w-md truncate text-xs text-slate-500">{t.description}</div></Link></td>
              <td className="px-4 py-3"><Badge>{t.category}</Badge></td>
              <td className="px-4 py-3 font-semibold">{t.bounty} ETH</td>
              <td className="px-4 py-3 text-slate-600">{t.deadline}</td>
              <td className="px-4 py-3 text-slate-600">{t.bids}</td>
              <td className="px-4 py-3"><Badge>{t.status}</Badge></td>
            </tr>
          ))}
          {!list.length && <tr><td colSpan={6} className="p-8 text-center text-slate-400">No tasks found</td></tr>}
        </tbody>
      </table>
    </div>
  );
}
