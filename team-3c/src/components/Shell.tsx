'use client';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { createContext, useContext, useState } from 'react';
import { stats } from '@/data/mockData';
const Ctx = createContext({ connected: true, toggle: () => {}, toast: (_m: string) => {} });
export const useApp = () => useContext(Ctx);
const nav = [['/', 'Home', '🏠'], ['/marketplace', 'Task Marketplace', '📋'], ['/my-tasks', 'My Tasks', '📄'], ['/agents', 'Agents', '🤖'], ['/wallet', 'Wallet', '👛'], ['/analytics', 'Analytics', '📊'], ['/settings', 'Settings', '⚙️']];
export default function Shell({ children }: { children: React.ReactNode }) {
  const path = usePathname(); const router = useRouter();
  const [connected, setConnected] = useState(true); const [msg, setMsg] = useState(''); const [q, setQ] = useState('');
  const toast = (m: string) => { setMsg(m); setTimeout(() => setMsg(''), 2500); };
  const toggle = () => { setConnected(!connected); toast(connected ? 'Wallet disconnected' : 'Wallet connected (mock)'); };
  return (
    <Ctx.Provider value={{ connected, toggle, toast }}>
      <div className="flex min-h-screen bg-slate-50 text-slate-800">
        <aside className="sticky top-0 hidden h-screen w-60 shrink-0 flex-col border-r border-slate-200 bg-gradient-to-b from-white to-indigo-50 p-4 md:flex">
          <div className="mb-6 flex items-center gap-2 px-2 text-lg font-extrabold leading-tight text-slate-900"><span className="grid h-9 w-9 place-items-center rounded-xl bg-gradient-to-br from-violet-600 to-blue-500 text-white">◆</span>Intelligence<br />Exchange</div>
          <nav className="space-y-1">{nav.map(([href, label, icon]) => {
            const active = href === '/' ? path === '/' : path.startsWith(href);
            return <Link key={href} href={href} className={`flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium ${active ? 'bg-violet-100 text-violet-700' : 'text-slate-600 hover:bg-white'}`}><span>{icon}</span>{label}</Link>;
          })}</nav>
          <div className="mt-auto rounded-2xl bg-white/80 p-4 text-sm"><b className="text-slate-900">AI Agents for a Better World</b><p className="mt-1 text-xs text-slate-500">Decentralized. Transparent. Human-Aligned.</p></div>
        </aside>
        <div className="min-w-0 flex-1">
          <header className="flex items-center gap-3 border-b border-slate-200 bg-white px-6 py-3">
            <input value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && router.push(`/marketplace?q=${encodeURIComponent(q)}`)} placeholder="Search tasks, agents or categories..." className="w-full max-w-xl rounded-xl border border-slate-200 bg-slate-50 px-4 py-2 text-sm outline-none focus:border-violet-400" />
            <div className="ml-auto flex items-center gap-3">
              <span className="rounded-xl border border-slate-200 px-3 py-2 text-sm">⟠ Sepolia</span>
              <button onClick={toggle} className={`rounded-xl px-3 py-2 text-sm font-medium ${connected ? 'border border-slate-200' : 'bg-violet-600 text-white'}`}>{connected ? <>🟣 {stats.address}</> : 'Connect Wallet'}</button>
            </div>
          </header>
          <main className="p-6">{children}</main>
        </div>
        {msg && <div className="fixed bottom-6 right-6 z-50 rounded-xl bg-slate-900 px-4 py-3 text-sm text-white shadow-lg">{msg}</div>}
      </div>
    </Ctx.Provider>
  );
}
