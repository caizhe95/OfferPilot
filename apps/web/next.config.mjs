/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  async rewrites() {
    const apiBaseUrl = process.env.API_BASE_URL || "http://localhost:8000";
    const agentBaseUrl = process.env.AGENT_BASE_URL || "http://localhost:3001";
    return [
      {
        source: "/api/:path*",
        destination: `${apiBaseUrl}/api/:path*`,
      },
      {
        source: "/agent/:path*",
        destination: `${agentBaseUrl}/agent/:path*`,
      },
    ];
  },
};

export default nextConfig;
