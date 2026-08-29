import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'Yukai — Autonomous Coding Agent',
  description: 'A lightweight autonomous coding agent with a visual workspace for tool calls, approvals, and file changes.',
  authors: [{ name: 'Feng Yikang' }],
  creator: 'Feng Yikang',
  openGraph: {
    title: 'Yukai',
    description: 'A Lightweight Autonomous Coding Agent',
    images: ['https://raw.githubusercontent.com/2022211932/fyk-coding-agent/main/web/public/og.png'],
  },
  twitter: {
    card: 'summary_large_image',
    title: 'Yukai',
    description: 'A Lightweight Autonomous Coding Agent',
    images: ['https://raw.githubusercontent.com/2022211932/fyk-coding-agent/main/web/public/og.png'],
  },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
