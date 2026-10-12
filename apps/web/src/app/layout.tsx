import type { Metadata } from 'next';

import { AuthProvider } from '@/components/AuthProvider';

import './globals.css';

export const metadata: Metadata = {
  title: 'HPC Platform',
  description: 'HPC 作业调度与计费管理平台',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body className="min-h-screen antialiased">
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}
