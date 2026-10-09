import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Build output folder (default .next). NEXT_DIST_DIR lets a verification build run without touching the live build.
  distDir: process.env.NEXT_DIST_DIR || ".next",
  poweredByHeader: false,
  // Hide the Next.js dev indicator (compile/runtime errors are still shown in development).
  devIndicators: false,
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "X-Frame-Options", value: "DENY" },
        ],
      },
    ];
  },
};

export default nextConfig;
