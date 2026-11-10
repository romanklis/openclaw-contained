/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
  env: {
    API_URL: process.env.NEXT_PUBLIC_API_URL || 'http://localhost/api',
  },
  async rewrites() {
    const controlPlane =
      process.env.CONTROL_PLANE_INTERNAL_URL || 'http://control-plane:8000'
    return [
      { source: '/api/:path*', destination: `${controlPlane}/api/:path*` },
    ]
  },
  async redirects() {
    return [
      { source: '/agent-images', destination: '/image-catalog', permanent: false },
    ]
  },
}

module.exports = nextConfig
