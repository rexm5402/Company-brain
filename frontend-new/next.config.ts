import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Next.js dev server runs on port 3000; FastAPI on 8077
  // Static export is not used because dynamic routes (/tickets/[id]) need
  // client-side data fetching — serve via `next start` or `next dev`.
  images: { unoptimized: true },
};

export default nextConfig;
