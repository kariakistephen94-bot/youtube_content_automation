import type { NextConfig } from "next";

// The Python API (`uv run tutorial-ai serve`) listens on 127.0.0.1:8765. Proxying /api through
// Next keeps the browser on one origin, so the API never needs to allow cross-origin requests.
const API_URL = process.env.TUTORIAL_AI_API_URL ?? "http://127.0.0.1:8765";

const nextConfig: NextConfig = {
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_URL}/api/:path*` }];
  },
};

export default nextConfig;
