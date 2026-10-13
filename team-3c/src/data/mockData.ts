export type Status = 'Open' | 'In Progress' | 'Completed' | 'Expired';
export const categories = ['Research', 'Data', 'Machine Learning', 'Strategy', 'Development', 'Web3'];
const T = (id: number, title: string, description: string, category: string, bounty: number, deadline: string, bids: number, status: Status, mine = false, agent?: string) =>
  ({ id, title, description, category, bounty, deadline, bids, status, mine, agent, creator: '0x3f2...9a7D', created: '2 days ago', funded: status !== 'Expired', caps: ['Research', 'Python', 'Data Analysis'] });
export const tasks = [
  T(123, 'Research latest LLM trends', 'Compile a detailed report on recent advances in LLM architectures and tooling.', 'Research', 10, '2h', 4, 'Open', true),
  T(122, 'Analyze on-chain data', 'Data analysis of DeFi protocol activity across major L2s.', 'Data', 7, '5h', 2, 'Open', true),
  T(121, 'Train a sentiment model', 'Build and evaluate a sentiment model for crypto social posts.', 'Machine Learning', 15, '1d', 7, 'Open'),
  T(120, 'Create marketing strategy', 'Develop a go-to-market plan for a Web3 product launch.', 'Strategy', 5, '12h', 3, 'Open'),
  T(119, 'Build a trading bot', 'Design and backtest a simple arbitrage bot.', 'Development', 12, '1d', 6, 'Open', true),
  T(118, 'Audit ERC-20 contract', 'Review a token contract for common vulnerabilities.', 'Web3', 9, '3d', 5, 'In Progress', true, 'Agent #08'),
  T(117, 'Summarize governance proposals', 'Weekly digest of DAO proposals with risk notes.', 'Research', 4, '6h', 2, 'In Progress', false, 'Agent #12'),
  T(116, 'Clean NFT sales dataset', 'Normalize and dedupe 2M NFT sale records.', 'Data', 6, '-', 8, 'Completed', true, 'Agent #05'),
  T(115, 'Competitor landscape report', 'Map the top 10 AI-agent marketplaces.', 'Strategy', 8, '-', 4, 'Completed', false, 'Agent #17'),
  T(114, 'Fine-tune embeddings model', 'Domain-tune an embeddings model for search.', 'Machine Learning', 14, 'Expired', 0, 'Expired', true),
  T(113, 'Analyze DeFi protocol', 'Risk and yield analysis of a lending protocol.', 'Web3', 11, '2d', 3, 'Open'),
  T(112, 'Landing page copy', 'Write copy for a product landing page.', 'Strategy', 3, '8h', 1, 'Open'),
];
const A = (n: number, status: string, caps: string[], wallet: string, rep: number, done: number, success: number, earnings: number, current: number) =>
  ({ id: n, name: `Agent #${String(n).padStart(2, '0')}`, status, caps, wallet, rep, done, success, earnings, current, bids: done * 3, won: done + 4, failed: Math.round(done * (100 - success) / 100), avg: '2h 10m' });
export const agents = [
  A(8, 'ACTIVE', ['Research', 'Python', 'Data Analysis'], '0x1a4...c3d2', 4.9, 58, 97, 142.5, 2),
  A(12, 'ACTIVE', ['ML', 'Python'], '0x9b2...e7f1', 4.8, 47, 95, 118.2, 1),
  A(5, 'BUSY', ['Web3', 'Solidity'], '0x7c1...a9b3', 4.7, 41, 93, 96.4, 3),
  A(17, 'ACTIVE', ['Research', 'Python', 'Data Analysis'], '0x3d5...f0e1', 4.6, 35, 91, 80.9, 1),
  A(21, 'IDLE', ['Market Analysis', 'Research'], '0x6f8...d4a9', 4.5, 29, 90, 64.0, 0),
  A(3, 'ACTIVE', ['Data Analysis', 'ML', 'Web3'], '0x2e7...b5c8', 4.4, 22, 88, 51.3, 1),
];
export const allCaps = ['Research', 'Python', 'Data Analysis', 'ML', 'Web3', 'Solidity', 'Market Analysis'];
export const bids = [
  { agent: 17, amount: 7, eta: '1h 40m', rep: 4.6, status: 'Submitted' },
  { agent: 8, amount: 8, eta: '2h', rep: 4.9, status: 'Submitted' },
  { agent: 5, amount: 6.5, eta: '3h', rep: 4.7, status: 'Submitted' },
];
export const activities = [
  { text: 'Agent #17 placed a bid on Research #01', time: '2 min ago', icon: '🤖' },
  { text: 'Task #22 assigned to Agent #08', time: '12 min ago', icon: '📄' },
  { text: 'Settlement completed for Task #18', time: '35 min ago', icon: '✅' },
  { text: 'New task posted: "Analyze DeFi protocol"', time: '1 hr ago', icon: '➕' },
  { text: 'Agent #05 completed a task', time: '2 hr ago', icon: '🤖' },
];
export const transactions = [
  { hash: '0xa1...91f', type: 'Funding', desc: 'Funded Task #123', amount: -10, time: '2h ago' },
  { hash: '0xb7...c02', type: 'Settlement', desc: 'Refund Task #114', amount: 14, time: '1d ago' },
  { hash: '0xc3...5de', type: 'Funding', desc: 'Funded Task #119', amount: -12, time: '2d ago' },
  { hash: '0xd9...a17', type: 'Settlement', desc: 'Settled Task #116', amount: -6, time: '3d ago' },
];
export const stats = { activeTasks: 24, activeAgents: 12, totalBounty: 180, completed: 36, balance: 42.8, address: '0x3f2...9a7D' };
