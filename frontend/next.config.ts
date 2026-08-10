import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  typedRoutes: true,
  allowedDevOrigins: ["127.0.0.1"],
  async rewrites() {
    const apiOrigin = process.env.MERIDIAN_API_ORIGIN;
    return apiOrigin ? [{ source: "/api/v1/:path*", destination: `${apiOrigin}/api/v1/:path*` }] : [];
  },
};
export default nextConfig;
