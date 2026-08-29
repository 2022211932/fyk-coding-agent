import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'FYK Agent Console',
  description: 'Visual workspace for FYK Coding Agent sessions, tool calls, and file changes.',
  authors: [{ name: 'Feng Yikang' }],
  creator: 'Feng Yikang',
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
