import type { NextConfig } from "next";

// Production (single Vercel project, Services): vercel.json routes /api/* to the
// FastAPI service on the same domain, so no rewrite is needed here.
// Local dev: set API_URL (e.g. http://127.0.0.1:8000) to proxy /api/* to uvicorn.
const API_URL = process.env.API_URL?.replace(/\/$/, "");

const nextConfig: NextConfig = {
  async rewrites() {
    return API_URL ? [{ source: "/api/:path*", destination: `${API_URL}/api/:path*` }] : [];
  },
};

export default nextConfig;
