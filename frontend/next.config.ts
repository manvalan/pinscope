import type { NextConfig } from "next";
import {
  CSP_CONNECT_HOSTS,
  CSP_FRAME_HOSTS,
  CSP_SCRIPT_HOSTS,
} from "./src/lib/csp-hosts";

const extra = (hosts: string[]) => (hosts.length ? ` ${hosts.join(" ")}` : "");

const nextConfig: NextConfig = {
  serverExternalPackages: ["pdfjs-dist"],
  turbopack: {
    resolveAlias: {
      canvas: { browser: "" },
    },
  },
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          {
            key: "Strict-Transport-Security",
            value: "max-age=31536000; includeSubDomains",
          },
          {
            key: "Content-Security-Policy",
            value: [
              "default-src 'self'",
              "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://unpkg.com https://vercel.live" +
                extra(CSP_SCRIPT_HOSTS),
              "style-src 'self' 'unsafe-inline' https://vercel.live https://fonts.googleapis.com",
              "img-src 'self' data: https: blob:",
              "font-src 'self' data: https://vercel.live https://assets.vercel.com https://fonts.gstatic.com",
              "connect-src 'self' blob: https://storage.googleapis.com https://vercel.live wss://ws-us3.pusher.com" +
                extra(CSP_CONNECT_HOSTS),
              "frame-src 'self' https://vercel.live" + extra(CSP_FRAME_HOSTS),
              "worker-src 'self' blob:",
            ].join("; "),
          },
        ],
      },
    ];
  },
};

export default nextConfig;
