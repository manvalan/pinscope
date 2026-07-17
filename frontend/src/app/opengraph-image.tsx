import { ImageResponse } from "next/og";

export const alt = "Pinscope — Agentic schematic validation";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

export default function Image() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          padding: "72px",
          background:
            "radial-gradient(at 30% 20%, #15233f 0%, #0a0a0a 55%, #050505 100%)",
          color: "#fafafa",
          fontFamily: "sans-serif",
        }}
      >
        {/* Brand row */}
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          <svg
            width="40"
            height="40"
            viewBox="0 0 24 24"
            fill="none"
            stroke="#3b82f6"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <rect x="4" y="4" width="16" height="16" rx="2" />
            <rect x="9" y="9" width="6" height="6" />
            <path d="M9 2v2" />
            <path d="M15 2v2" />
            <path d="M9 20v2" />
            <path d="M15 20v2" />
            <path d="M20 9h2" />
            <path d="M20 15h2" />
            <path d="M2 9h2" />
            <path d="M2 15h2" />
          </svg>
          <div
            style={{
              fontSize: 36,
              fontWeight: 600,
              letterSpacing: "-0.01em",
            }}
          >
            Pinscope
          </div>
        </div>

        {/* Main copy */}
        <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
          <div
            style={{
              fontSize: 88,
              fontWeight: 600,
              lineHeight: 1.05,
              letterSpacing: "-0.03em",
              maxWidth: 980,
            }}
          >
            Ship hardware that works the first time.
          </div>
          <div
            style={{
              fontSize: 30,
              lineHeight: 1.3,
              color: "#a1a1aa",
              maxWidth: 880,
            }}
          >
            Datasheet-grounded schematic review. Catches the errors that
            would otherwise surface at bring-up.
          </div>
        </div>

        {/* Footer row */}
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            fontSize: 22,
            color: "#71717a",
            borderTop: "1px solid #27272a",
            paddingTop: 24,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            KiCad · Altium · OrCAD · Cadence · Siemens · EasyEDA · EAGLE
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            pinscope.ai
          </div>
        </div>
      </div>
    ),
    { ...size },
  );
}
