/** @type {import('next').NextConfig} */
const API_PROXY_TARGET = process.env.API_PROXY_TARGET ?? 'http://localhost:8000';

const nextConfig = {
  reactStrictMode: true,
  // Required by the Dockerfile (copies .next/standalone).
  output: 'standalone',
  // In production Caddy serves /api before the request reaches Next.js; this
  // rewrite only matters for `npm run dev` against a locally running API.
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: `${API_PROXY_TARGET}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
