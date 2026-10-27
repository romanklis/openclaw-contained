/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
  env: {
    API_URL: process.env.NEXT_PUBLIC_API_URL || 'http://localhost/api',
  },
  async redirects() {
    return [
      { source: '/agent-images', destination: '/image-catalog', permanent: false },
    ]
  },
}

module.exports = nextConfig
